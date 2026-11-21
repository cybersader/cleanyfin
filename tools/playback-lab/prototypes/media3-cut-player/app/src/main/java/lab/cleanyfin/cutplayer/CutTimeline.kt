package lab.cleanyfin.cutplayer

import org.json.JSONObject

data class Span(val startUs: Long, val endUs: Long) {
    init { require(startUs >= 0 && endUs > startUs) }
    val durationUs get() = endUs - startUs
    operator fun contains(positionUs: Long) = positionUs in startUs until endUs
}

data class Landing(val item: Int, val itemUs: Long, val filteredUs: Long, val sourceUs: Long)

/** Half-open ranges on the original media clock, and their concatenated seek coordinates. */
class CutTimeline private constructor(
    val sha256: String,
    val totalFrames: Int,
    val frameUs: Long,
    val sampleRate: Int,
    val mark: Span,
) {
    val durationUs = totalFrames * frameUs
    val original = listOf(Span(0, durationUs))
    val kept = listOf(Span(0, mark.startUs), Span(mark.endUs, durationUs))

    fun seek(sourceUs: Long, filtered: Boolean = true): Landing {
        val source = sourceUs.coerceIn(0, durationUs)
        if (!filtered) return Landing(0, source, source, source)
        if (source < mark.startUs) return Landing(0, source, source, source)
        val snapped = maxOf(mark.endUs, source)
        return Landing(1, snapped - mark.endUs, snapped - mark.durationUs, snapped)
    }

    /** Media3 period-local PTS preserves the original MP4 source time in a clip. */
    fun frameAt(sourceUs: Long): Int? {
        if (sourceUs !in -1..durationUs) return null
        val index = (sourceUs + frameUs / 2) / frameUs
        return index.takeIf { it < totalFrames && kotlin.math.abs(it * frameUs - sourceUs) <= 1 }
            ?.toInt()
    }

    companion object {
        fun parse(text: String): CutTimeline {
            val root = JSONObject(text)
            val source = root.getJSONObject("source")
            val cuts = root.getJSONArray("cuts")
            require(root.getInt("version") == 0 && cuts.length() == 1) { "Expected single-cut lab v0" }
            val cut = cuts.getJSONObject(0)
            val mux = cut.getJSONObject("muxedVideo")
            val fps = source.getInt("fps")
            val rate = source.getInt("sampleRate")
            require(fps == 25 && rate == 48_000) { "Expected the 25 fps / 48 kHz fixture" }
            val frameUs = 1_000_000L / fps
            val total = source.getInt("frames")
            val first = cut.getInt("startFrame")
            val exclusiveEnd = cut.getInt("endFrame")
            val hash = source.getString("sha256")
            require(total in 3..1024 && first in 1 until exclusiveEnd && exclusiveEnd < total)
            require(hash.matches(Regex("[0-9a-f]{64}"))) { "Invalid source hash" }
            val base = mux.getString("timeBase").split('/')
            require(base.size == 2)
            val (num, den) = base.map(String::toLong)
            require(num > 0 && den > 0)
            fun muxUs(key: String): Long {
                val n = Math.multiplyExact(Math.multiplyExact(mux.getLong(key), num), 1_000_000L)
                require(n % den == 0L) { "Fixture video PTS must have exact microsecond positions" }
                return n / den
            }
            val start = muxUs("firstMarkedPts")
            val end = Math.addExact(muxUs("lastMarkedPts"), frameUs)
            // Reject a shifted origin or inconsistent times: the fixture's audio clock starts at 0.
            require(start == first * frameUs && end == exclusiveEnd * frameUs &&
                cut.getLong("startMs") == start / 1000 && cut.getLong("endMs") == end / 1000) {
                "Muxed fixture timestamps do not match the plan's zero-origin frame grid"
            }
            return CutTimeline(hash, total, frameUs, rate, Span(start, end))
        }
    }
}

/** Nearest source sample recovers integer ticks from Media3's microsecond PCM PTS. */
internal fun sampleIndex(positionUs: Long, sampleRate: Int): Long =
    Math.floorDiv(positionUs * sampleRate + 500_000, 1_000_000)

/** Slice a contiguous decoder packet; cannot recover samples never delivered by Media3. */
internal fun keptPcmSlice(first: Long, frames: Int, cutStart: Long, cutEnd: Long): IntRange {
    val end = first + frames
    if (end <= cutStart || first >= cutEnd) return 0 until frames
    require(first >= cutStart || end <= cutEnd) { "Unexpected PCM packet spanning the entire cut" }
    return if (first < cutStart) 0 until (cutStart - first).toInt()
    else (cutEnd - first).coerceAtMost(frames.toLong()).toInt() until frames
}
