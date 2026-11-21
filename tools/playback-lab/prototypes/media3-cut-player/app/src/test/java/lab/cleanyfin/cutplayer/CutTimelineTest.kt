package lab.cleanyfin.cutplayer

import org.junit.Assert.*
import org.junit.Test

class CutTimelineTest {
    private val plan = """
        {"version":0,"source":{"sha256":"${"0".repeat(64)}","frames":500,"fps":25,"sampleRate":48000},
          "cuts":[{"startFrame":137,"endFrame":263,"startMs":5480,"endMs":10520,
           "muxedVideo":{"timeBase":"1/12800","firstMarkedPts":70144,"lastMarkedPts":134144}}]}
    """.trimIndent()
    private val timeline = CutTimeline.parse(plan)

    @Test fun keptRangesUseMuxedPts() {
        assertEquals(listOf(Span(0, 5_480_000), Span(10_520_000, 20_000_000)), timeline.kept)
        assertEquals(14_960_000L, timeline.kept.sumOf { it.durationUs })
        assertEquals(136, timeline.frameAt(5_440_000))
        assertEquals(263, timeline.frameAt(10_520_000))
        assertNull(timeline.frameAt(10_521_000))
    }

    @Test fun seeksUseKeptTimelineAndWindowLocalCoordinates() {
        assertEquals(Landing(0, 5_000_000, 5_000_000, 5_000_000), timeline.seek(5_000_000))
        assertEquals(Landing(1, 480_000, 5_960_000, 11_000_000), timeline.seek(11_000_000))
        assertEquals(Landing(1, 9_480_000, 14_960_000, 20_000_000), timeline.seek(20_000_000))
        assertEquals(Landing(0, 7_000_000, 7_000_000, 7_000_000), timeline.seek(7_000_000, false))
    }

    @Test fun seeksInsideOrAtBoundarySnapToFirstKeptInstant() {
        val expected = Landing(1, 0, 5_480_000, 10_520_000)
        for (source in listOf(5_480_000L, 7_000_000L, 10_519_999L, 10_520_000L)) {
            assertEquals(expected, timeline.seek(source))
        }
    }

    @Test(expected = IllegalArgumentException::class)
    fun planCannotSilentlyUseNominalTimesWhenMuxedPtsDisagree() {
        CutTimeline.parse(plan.replace("134144", "133632"))
    }

    @Test fun trimsDecoderBuffersToWholeSampleFrames() {
        val start = sampleIndex(timeline.mark.startUs, 48_000)
        val end = sampleIndex(timeline.mark.endUs, 48_000)
        // Source AAC packet starts at sample 262144; PTS truncated to microseconds.
        assertEquals(262_144L, sampleIndex(5_461_333, 48_000))
        assertEquals(0 until 896, keptPcmSlice(262_144, 1024, start, end))
        assertEquals(128 until 1024, keptPcmSlice(504_832, 1024, start, end))
        assertTrue(keptPcmSlice(start, 1024, start, end).isEmpty())
        assertEquals(0 until 1024, keptPcmSlice(end, 1024, start, end))
    }
}
