package lab.cleanyfin.cutplayer

import android.content.Context
import android.media.AudioFormat
import androidx.media3.common.C
import androidx.media3.common.MimeTypes
import androidx.media3.common.audio.AudioProcessor
import androidx.media3.common.audio.BaseAudioProcessor
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.audio.AudioOutput
import androidx.media3.exoplayer.audio.AudioOutputProvider
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.audio.AudioTrackAudioOutputProvider
import androidx.media3.exoplayer.audio.DefaultAudioSink
import androidx.media3.exoplayer.audio.ForwardingAudioOutput
import androidx.media3.exoplayer.audio.ForwardingAudioOutputProvider
import androidx.media3.exoplayer.audio.ForwardingAudioSink
import java.nio.ByteBuffer
import java.nio.ByteOrder

@UnstableApi
internal class ProbeRenderers(
    context: Context, private val capture: RunCapture, private val pcmCut: CutTimeline?,
) : DefaultRenderersFactory(context) {
    override fun buildAudioSink(context: Context, enableFloatOutput: Boolean,
                                enableAudioOutputPlaybackParams: Boolean): AudioSink {
        val platformProvider = AudioTrackAudioOutputProvider.Builder(context).build()
        val tee = object : ForwardingAudioOutputProvider(platformProvider) {
            override fun getFormatSupport(config: AudioOutputProvider.FormatConfig): AudioOutputProvider.FormatSupport {
                if (config.format.sampleMimeType != MimeTypes.AUDIO_RAW) {
                    return AudioOutputProvider.FormatSupport.UNSUPPORTED
                }
                return super.getFormatSupport(config)
            }

            override fun getAudioOutput(config: AudioOutputProvider.OutputConfig): AudioOutput {
                val channels = Integer.bitCount(config.channelMask)
                check(config.encoding == C.ENCODING_PCM_16BIT && config.sampleRate == 48_000 &&
                    config.channelMask in setOf(AudioFormat.CHANNEL_OUT_MONO, AudioFormat.CHANNEL_OUT_STEREO) &&
                    !config.isTunneling && !config.isOffload) { "Unexpected AudioTrack output format" }
                val track = super.getAudioOutput(config)
                return object : ForwardingAudioOutput(track) {
                    override fun write(buffer: ByteBuffer, count: Int, ptsUs: Long): Boolean {
                        // View the bytes BEFORE the write; record only the part AudioTrack accepts.
                        val tapped = buffer.duplicate().order(ByteOrder.LITTLE_ENDIAN)
                        val handled = super.write(buffer, count, ptsUs)
                        tapped.limit(buffer.position())
                        capture.audio(tapped, channels)
                        return handled
                    }
                }
            }
        }
        val sink = DefaultAudioSink.Builder(context)
            .setEnableFloatOutput(false)
            .setEnableAudioOutputPlaybackParameters(false)
            .setAudioOutputProvider(tee)
            .build()
        return pcmCut?.let { TimestampedPcmSink(sink, PcmCutProcessor(it), capture) } ?: sink
    }
}

/** AudioProcessor has no PTS input; the sink adapter supplies one for each decoder buffer. */
@UnstableApi
private class PcmCutProcessor(timeline: CutTimeline) : BaseAudioProcessor() {
    private val rate = timeline.sampleRate
    private val start = sampleIndex(timeline.mark.startUs, rate)
    private val end = sampleIndex(timeline.mark.endUs, rate)
    var sourceUs = C.TIME_UNSET
    var skippedAtHead = 0
        private set
    var removed = 0
        private set

    override fun onConfigure(input: AudioProcessor.AudioFormat): AudioProcessor.AudioFormat {
        require(input.encoding == C.ENCODING_PCM_16BIT && input.sampleRate == rate &&
            input.channelCount in 1..2) { "cut_pcm requires mono or stereo 48 kHz s16 PCM" }
        return input
    }

    override fun queueInput(input: ByteBuffer) {
        val bytesPerFrame = inputAudioFormat.bytesPerFrame
        check(input.remaining() % bytesPerFrame == 0)
        val frames = input.remaining() / bytesPerFrame
        val first = sampleIndex(sourceUs, rate)
        val kept = keptPcmSlice(first, frames, start, end)
        skippedAtHead = kept.first
        removed = frames - kept.count()
        val out = replaceOutputBuffer(kept.count() * bytesPerFrame)
        if (!kept.isEmpty()) {
            val initial = input.position()
            input.position(initial + kept.first * bytesPerFrame)
            input.limit(initial + (kept.last + 1) * bytesPerFrame)
            out.put(input)
        }
        out.flip()
    }

    fun headOffsetUs() = skippedAtHead * 1_000_000L / rate
}

@UnstableApi
private class TimestampedPcmSink(
    sink: AudioSink, private val pcm: PcmCutProcessor, private val capture: RunCapture,
) : ForwardingAudioSink(sink) {
    private var streamOffsetUs = C.TIME_UNSET
    private var pendingInput: ByteBuffer? = null
    private var pendingOutput: ByteBuffer? = null
    private var pendingPtsUs = C.TIME_UNSET

    override fun configure(config: AudioSink.AudioSinkConfig) {
        check(pendingInput == null)
        val format = config.format
        pcm.configure(AudioProcessor.AudioFormat(format.sampleRate, format.channelCount, format.pcmEncoding))
        pcm.flush(AudioProcessor.StreamMetadata.DEFAULT)
        super.configure(config)
    }

    override fun setOutputStreamOffsetUs(offsetUs: Long) {
        streamOffsetUs = offsetUs
        super.setOutputStreamOffsetUs(offsetUs)
    }

    override fun handleBuffer(buffer: ByteBuffer, ptsUs: Long, count: Int): Boolean {
        if (pendingInput == null) {
            if (!buffer.hasRemaining()) return true
            check(streamOffsetUs != C.TIME_UNSET && ptsUs != C.TIME_UNSET) { "Missing decoder PCM PTS" }
            pcm.sourceUs = ptsUs - streamOffsetUs
            pcm.queueInput(buffer.duplicate())
            capture.trimmed(pcm.removed)
            val trimmed = pcm.output
            if (!trimmed.hasRemaining()) {
                buffer.position(buffer.limit())
                return true
            }
            pendingInput = buffer
            pendingOutput = if (pcm.removed == 0) buffer else trimmed
            pendingPtsUs = ptsUs + pcm.headOffsetUs()
        } else {
            check(pendingInput === buffer) { "Audio sink retry supplied another PCM buffer" }
        }
        val output = checkNotNull(pendingOutput)
        if (!super.handleBuffer(output, pendingPtsUs, count)) return false
        buffer.position(buffer.limit())
        discardPending()
        return true
    }

    override fun flush() {
        discardPending()
        pcm.flush(AudioProcessor.StreamMetadata.DEFAULT)
        super.flush()
    }

    override fun reset() {
        discardPending()
        // DefaultAudioSink.reset() retains its configured format; do the same here.
        pcm.flush(AudioProcessor.StreamMetadata.DEFAULT)
        super.reset()
    }

    private fun discardPending() {
        pendingInput = null
        pendingOutput = null
        pendingPtsUs = C.TIME_UNSET
    }
}
