package lab.cleanyfin.cutplayer

import android.os.Build
import androidx.media3.common.MediaLibraryInfo
import androidx.media3.common.util.UnstableApi
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer

/** One bounded synthetic fixture run; keep disk I/O off the playback thread. */
@UnstableApi
internal class RunCapture(private val directory: File, mode: String, seek: String) {
    val info = JSONObject()
        .put("mode", mode).put("seek", seek)
        .put("deviceModel", Build.MODEL).put("apiLevel", Build.VERSION.SDK_INT)
        .put("media3Version", MediaLibraryInfo.VERSION)
        .put("tunnelingActive", JSONObject.NULL)
        .put("passthroughActive", JSONObject.NULL)
        .put("offloadActive", JSONObject.NULL)
        .put("audioPcmFormat", "48000 Hz mono s16le")
    val errors = JSONArray()
    val videoDecoders = linkedSetOf<String>()
    val audioDecoders = linkedSetOf<String>()

    private val frames = StringBuilder("presentation_time_us,release_time_ns,source_frame_index,inside_mark\n")
    // 20 seconds plus replay after `back` stays below 30 seconds; reserve a 40-second hard cap.
    private val pcm = ByteArray(40 * 48_000 * 2)
    private var pcmSize = 0
    private var frameCount = 0
    private var unmappedCount = 0
    private var markedCount = 0
    private var trimmedSamples = 0
    private var closed = false

    @Synchronized
    fun trimmed(count: Int) {
        if (!closed) trimmedSamples += count
    }

    @Synchronized
    fun frame(ptsUs: Long, releaseNs: Long, timeline: CutTimeline) {
        if (closed) return
        check(frameCount < 2000) { "Exceeded fixture frame capture limit" }
        val index = timeline.frameAt(ptsUs)
        frameCount++
        if (index == null) unmappedCount++
        val marked = (index?.let { it * timeline.frameUs } ?: ptsUs) in timeline.mark
        if (marked) markedCount++
        frames.append(ptsUs).append(',').append(releaseNs).append(',').append(index ?: "")
            .append(',').append(marked).append('\n')
    }

    @Synchronized
    fun audio(buffer: ByteBuffer, channels: Int) {
        if (closed) return
        check(buffer.remaining() % (2 * channels) == 0) { "AudioTrack wrote a partial PCM frame" }
        check(pcmSize + buffer.remaining() / channels <= pcm.size) { "Exceeded fixture PCM capture limit" }
        while (buffer.hasRemaining()) {
            val left = buffer.short.toInt()
            val mono = if (channels == 1) left else (left + buffer.short.toInt()) / 2
            pcm[pcmSize++] = mono.toByte()
            pcm[pcmSize++] = (mono shr 8).toByte()
        }
    }

    @Synchronized
    fun complete(requestedStatus: String) {
        if (closed) return
        closed = true
        if (requestedStatus == "ended") {
            if (frameCount == 0 || pcmSize == 0) errors.put("Missing frame or PCM evidence")
            if (unmappedCount != 0) errors.put("Unexpected video PTS not on the fixture frame grid")
            if (info.getString("seek") == "back" && !info.optBoolean("backFired")) {
                errors.put("Scheduled backward seek did not fire")
            }
        }
        val status = if (requestedStatus == "ended" && errors.length() > 0) "error" else requestedStatus
        File(directory, "frames.csv").writeText(frames.toString())
        File(directory, "audio.pcm").outputStream().use { it.write(pcm, 0, pcmSize) }
        info.put("status", status).put("errors", errors)
            .put("videoDecoders", JSONArray(videoDecoders)).put("audioDecoders", JSONArray(audioDecoders))
            .put("frameCallbacks", frameCount).put("unmappedFrames", unmappedCount)
            .put("markedFrameCallbacks", markedCount).put("audioSamples", pcmSize / 2)
            .put("pcmCutRemovedSamples", trimmedSamples)
        File(directory, "run.json").writeText(info.toString(2) + "\n")
        File(directory, "DONE").writeText("$status\n")
    }
}
