package lab.cleanyfin.cutplayer

import android.app.Activity
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Gravity
import android.view.SurfaceView
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.FrameLayout
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.TrackSelectionParameters.AudioOffloadPreferences
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.SeekParameters
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.exoplayer.audio.AudioSink
import androidx.media3.exoplayer.trackselection.DefaultTrackSelector
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.UUID

@UnstableApi
class MainActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private var player: ExoPlayer? = null
    private var capture: RunCapture? = null
    private var finished = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        val surface = SurfaceView(this)
        val frame = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            addView(surface, FrameLayout.LayoutParams(1, 1, Gravity.CENTER))
            addOnLayoutChangeListener { _, left, top, right, bottom, _, _, _, _ ->
                val width = minOf(right - left, (bottom - top) * 16 / 9)
                val height = width * 9 / 16
                val layout = surface.layoutParams
                if (width > 0 && height > 0 && (layout.width != width || layout.height != height)) {
                    layout.width = width
                    layout.height = height
                    surface.layoutParams = layout
                }
            }
        }
        setContentView(frame)
        hideBars()

        val mode = intent.getStringExtra("mode") ?: "cut"
        val seek = intent.getStringExtra("seek") ?: "none"
        try {
            val external = checkNotNull(getExternalFilesDir(null)) { "External files unavailable" }
            val id = intent.getStringExtra("run_id") ?: UUID.randomUUID().toString()
            require(id.matches(Regex("[A-Za-z0-9_-]{1,64}"))) { "Invalid run ID" }
            val runs = File(external, "runs")
            check(runs.isDirectory || runs.mkdir()) { "Cannot create runs directory" }
            val directory = File(runs, id)
            check(directory.mkdir()) { "Cannot create fresh run directory $id" }
            val run = RunCapture(directory, mode, seek)
            capture = run
            Log.i(TAG, "Run evidence: $directory")
            require(mode in setOf("original", "cut", "cut_pcm")) { "Unknown mode: $mode" }
            require(seek in setOf("none", "into", "back")) { "Unknown seek: $seek" }
            val timeline = CutTimeline.parse(File(external, "plan.json").readText())
            val source = File(external, "source.mp4")
            require(sha256(source) == timeline.sha256) { "source.mp4 does not match plan.json" }
            run.info.put("sourceSha256", timeline.sha256)
                .put("cutStartUs", timeline.mark.startUs).put("cutEndUs", timeline.mark.endUs)
            play(surface, source, timeline, mode, seek, run)
        } catch (e: Exception) {
            Log.e(TAG, "Could not start fixture", e)
            end("error", e.toString())
        }
    }

    private fun play(surface: SurfaceView, file: File, timeline: CutTimeline,
                     mode: String, seek: String, run: RunCapture) {
        val filtered = mode != "original"
        val renderers = ProbeRenderers(this, run, timeline.takeIf { mode == "cut_pcm" })
        val selector = DefaultTrackSelector(this).apply {
            setParameters(buildUponParameters().setTunnelingEnabled(false)
                .setAudioOffloadPreferences(AudioOffloadPreferences.DEFAULT))
        }
        val exo = ExoPlayer.Builder(this, renderers)
            .setTrackSelector(selector).setSeekParameters(SeekParameters.EXACT).build()
        player = exo
        exo.addAnalyticsListener(object : AnalyticsListener {
            override fun onAudioDecoderInitialized(eventTime: AnalyticsListener.EventTime, decoderName: String,
                                                   initializedTimestampMs: Long, initializationDurationMs: Long) {
                run.audioDecoders += decoderName
            }

            override fun onVideoDecoderInitialized(eventTime: AnalyticsListener.EventTime, decoderName: String,
                                                   initializedTimestampMs: Long, initializationDurationMs: Long) {
                run.videoDecoders += decoderName
            }

            override fun onAudioTrackInitialized(eventTime: AnalyticsListener.EventTime,
                                                 audioTrackConfig: AudioSink.AudioTrackConfig) {
                run.info.put("tunnelingActive", audioTrackConfig.tunneling || exo.isTunnelingEnabled)
                    .put("offloadActive", audioTrackConfig.offload)
                    .put("passthroughActive", audioTrackConfig.encoding != C.ENCODING_PCM_16BIT &&
                        !audioTrackConfig.offload)
            }
        })
        exo.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                if (playbackState == Player.STATE_ENDED) end("ended")
            }
            override fun onPlayerError(error: PlaybackException) {
                end("error", "${error.errorCodeName}: ${error.message}; ${error.cause}")
            }
        })
        exo.setVideoFrameMetadataListener { ptsUs, releaseNs, _, _ -> run.frame(ptsUs, releaseNs, timeline) }
        exo.setVideoSurfaceView(surface)

        val items = (if (filtered) timeline.kept else timeline.original).mapIndexed { index, range ->
            MediaItem.Builder().setMediaId("fixture-$index").setUri(Uri.fromFile(file)).apply {
                if (filtered) setClippingConfiguration(MediaItem.ClippingConfiguration.Builder()
                    .setStartPositionUs(range.startUs)
                    .setEndPositionUs(if (index == 0) range.endUs else C.TIME_END_OF_SOURCE)
                    .setStartsAtKeyFrame(false)
                    .setRelativeToDefaultPosition(false)
                    .setRelativeToLiveWindow(false)
                    .setAllowUnseekableMedia(false)
                    .build())
            }.build()
        }
        val start = timeline.seek(if (seek == "into") 7_000_000 else 0, filtered)
        run.info.put("initialSeek", start.json())
        exo.setMediaItems(items, start.item, start.itemUs / 1000)
        if (seek == "back") {
            val trigger = timeline.seek(timeline.mark.endUs + 1_000_000, filtered)
            val back = timeline.seek(5_000_000, filtered)
            run.info.put("backTrigger", trigger.json()).put("backTarget", back.json()).put("backFired", false)
            exo.createMessage { _, _ ->
                if (!finished) {
                    run.info.put("backFired", true)
                    exo.seekTo(back.item, back.itemUs / 1000)
                }
            }.setLooper(mainLooper).setPosition(trigger.item, trigger.itemUs / 1000)
                .setDeleteAfterDelivery(true).send()
        }
        handler.postDelayed({ end("error", "Fixture did not finish within 90 seconds") }, 90_000)
        exo.prepare()
        exo.play()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideBars()
    }

    @Suppress("DEPRECATION")
    private fun hideBars() {
        if (Build.VERSION.SDK_INT >= 30) {
            window.setDecorFitsSystemWindows(false)
            window.insetsController?.apply {
                systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                hide(WindowInsets.Type.systemBars())
            }
        } else {
            window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        }
    }

    override fun onStop() {
        super.onStop()
        if (!finished) end("interrupted", "Activity left the foreground")
    }

    private fun end(status: String, error: String? = null) {
        if (finished) return
        finished = true
        handler.removeCallbacksAndMessages(null)
        error?.let { capture?.errors?.put(it) }
        try {
            player?.release()
        } catch (e: Exception) {
            capture?.errors?.put("Release: $e")
        } finally {
            player = null
            try {
                capture?.complete(status)
            } catch (e: Exception) {
                Log.e(TAG, "Could not write run evidence", e)
            }
            finish()
        }
    }

    private fun Landing.json() = JSONObject().put("item", item).put("itemUs", itemUs)
        .put("filteredUs", filteredUs).put("sourceUs", sourceUs)

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val bytes = ByteArray(64 * 1024)
            while (true) {
                val size = input.read(bytes)
                if (size < 0) break
                digest.update(bytes, 0, size)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private companion object { const val TAG = "CleanyfinCutLab" }
}
