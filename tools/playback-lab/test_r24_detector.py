"""Unit tests for the R24 v2 marked-sound detector (marked_oracle + r24_detector). Synthetic arrays only."""
import unittest

import numpy as np

import format_fixtures as ff
import marked_oracle as mo
import r24_detector as detector


def code_source(n=40_000, channels=6, seed=7):
    """A small source-time-code uncut decode: tone on 0 and 3, independent codes elsewhere."""
    rng = np.random.default_rng(seed)
    u = rng.integers(-6000, 6000, (n, channels)).astype(np.int16)
    t = np.arange(n)
    u[:, 0] = (8000*np.sin(2*np.pi*440*t/48000)).astype(np.int16)
    u[:, 3] = (6000*np.sin(2*np.pi*440*t/48000)).astype(np.int16)
    return u


def packetize(y, size=1536, first=None):
    cuts = sorted({0, len(y), *range(0, len(y), size), *([first] if first else [])})
    return [(0, a, b) for a, b in zip(cuts, cuts[1:]) if b > a]


class Fixture(unittest.TestCase):
    def test_timecode_is_deterministic_and_unique_per_frame(self):
        a = ff.timecode_pcm(6)[:50_000]
        np.testing.assert_array_equal(a, ff.timecode_pcm(6)[:50_000])
        q = np.round(a*32767).astype(np.int16)
        self.assertEqual(len(np.unique(q.view(np.dtype((np.void, 12))))), len(q))
        self.assertAlmostEqual(float(np.abs(a[:, 1]).max()), ff.TIMECODE_LEVEL, places=3)
        # Channel 0 keeps the clock tone reference_lead fits.
        n = np.arange(1000)
        np.testing.assert_allclose(a[:1000, 0], 0.25*np.sin(2*np.pi*440*n/48000), atol=1e-6)

    def test_signature_refused_outside_dolby(self):
        with self.assertRaises(ValueError):
            ff.generate(None, 'mp4-h264-aac', 'timecode')
        with self.assertRaises(ValueError):
            ff.generate(None, 'mkv-h264-eac351', 'nonsense')


class Oracle(unittest.TestCase):
    def setUp(self):
        self.u = code_source()
        self.code = mo.SourceCode(self.u, lead=1)
        self.lo, self.hi = 10_001, 12_001

    def cut(self):
        return np.concatenate([self.u[1:self.lo], self.u[self.hi:]])

    def test_ideal_cut_has_zero_alarms(self):
        y, p = mo.ideal_cut(self.code, self.lo, self.hi)
        v = mo.judge(y, p, self.code, self.lo, self.hi)
        self.assertEqual((v.detections, v.presented - v.exactKept, v.duplicates, v.keptGapSamples), (0, 0, 0, 0))

    def test_one_escaped_sample_at_either_side_is_caught(self):
        y = self.cut()
        s = self.lo - 1  # output index of the last kept frame before the join
        for block, at, expect in ((self.u[self.lo:self.lo+1], s, self.lo), (self.u[self.hi-1:self.hi], s, self.hi-1)):
            with self.subTest(at=expect):
                y2 = np.concatenate([y[:at], block, y[at:]])
                v = mo.judge(y2, packetize(y2), self.code, self.lo, self.hi)
                self.assertEqual(v.detections, 1)
                self.assertEqual(v.markedPositions, [expect])

    def test_decoder_altered_prefix_is_caught_by_position_not_value(self):
        y = self.cut()
        at = self.lo - 1
        block = np.clip(self.u[self.hi-3:self.hi].astype(np.int32) + 400, -32768, 32767).astype(np.int16)
        # The altered run start is in the same packet as the following exact frames (one decoder run).
        y2 = np.concatenate([y[:at], block, y[at:]])
        v = mo.judge(y2, packetize(y2, first=at), self.code, self.lo, self.hi)
        self.assertEqual((v.positionalMarked, v.exactMarked, v.detections), (3, 0, 3))

    def test_one_frame_packet_from_the_run_before_the_join_is_kept(self):
        # Measured on MKV resume: a one-frame packet holding the last kept frame (2.8 LSB off), then the next run.
        y = np.concatenate([self.u[self.lo-1:self.lo] + np.int16(2), self.u[self.hi:self.hi+4000]])
        packets = [(0, 0, 1)] + [(0, a, b) for _, a, b in packetize(y[1:])]
        packets = [packets[0]] + [(0, a+1, b+1) for _, a, b in packets[1:]]
        v = mo.judge(y, packets, self.code, self.lo, self.hi)
        self.assertEqual(v.detections, 0)
        self.assertEqual(v.firstPosition, self.lo - 1)

    def test_ambiguous_altered_packet_is_unmeasured_not_clear(self):
        y = self.cut()
        noise = np.random.default_rng(1).integers(-6000, 6000, (64, 6)).astype(np.int16)
        at = self.lo - 1
        y2 = np.concatenate([y[:at], noise, y[at:]])
        # The noise is its own packet with no exact anchor; neither neighbouring run nor its value explains it.
        out = [(0, 0, at), (0, at, at + 64), (0, at + 64, len(y2))]
        with self.assertRaises(mo.Unmeasured):
            mo.judge(y2, out, self.code, self.lo, self.hi)

    def test_value_detector_catches_near_substitution_inside_a_run(self):
        y = self.cut()
        y2 = y.copy()
        y2[500] = np.clip(self.u[self.lo + 77].astype(np.int32) + 3, -32768, 32767)
        v = mo.judge(y2, packetize(y2), self.code, self.lo, self.hi, tau=32)
        self.assertEqual((v.valueMarked, v.detections), (1, 1))

    def test_incomplete_packet_trace_is_unmeasured(self):
        y = self.cut()
        with self.assertRaises(mo.Unmeasured):
            mo.judge(y, packetize(y)[1:], self.code, self.lo, self.hi)

    def test_repeated_frame_values_carry_no_identity(self):
        u = self.u.copy()
        u[20_000] = u[5]
        code = mo.SourceCode(u, 1)
        self.assertEqual(code.locate(u[[5, 6]]).tolist(), [-1, 6])

    def test_tau_is_bounded_by_fixture_separation(self):
        tau, closest = mo.tau_for(self.code, self.lo, self.hi)
        self.assertLessEqual(tau, mo.TAU_LSB)
        if closest is not None:
            self.assertLessEqual(tau, closest/2)

    def test_every_challenge_on_a_clean_take_is_detected(self):
        y = self.cut()
        res = mo.run_challenges(y, packetize(y), self.code, self.lo, self.hi, tau=32, interior=3)
        self.assertEqual(set(res) - set(mo.KINDS), set())
        for kind, row in res.items():
            with self.subTest(kind=kind):
                self.assertEqual(row['missed'], 0, row)
                self.assertGreater(row['planned'], 0)

    def test_challenges_follow_a_run_that_already_lost_kept_frames(self):
        # Measured on subtitle layouts: the run after the join started 128 kept frames late, its first 256 altered.
        after = self.u[self.hi + 128:].copy()
        after[:256] = np.clip(after[:256].astype(np.int32) + 9000, -32768, 32767)
        y = np.concatenate([self.u[1:self.lo], after])
        packets = packetize(y, first=self.lo - 1)
        v = mo.judge(y, packets, self.code, self.lo, self.hi)
        self.assertEqual((v.detections, v.keptLostAtJoins), (0, 128))
        res = mo.run_challenges(y, packets, self.code, self.lo, self.hi, tau=32, interior=1)
        self.assertEqual(sum(r['missed'] for r in res.values()), 0, res)
        self.assertEqual(res['after-join-prefix-altered']['exactCount'], res['after-join-prefix-altered']['planned'])

    def test_fresh_start_challenges(self):
        y = self.u[self.hi + 5:]
        res = mo.run_challenges(y, packetize(y), self.code, self.lo, self.hi, tau=32, interior=1)
        self.assertIn('start-early', res)
        self.assertEqual(sum(r['missed'] for r in res.values()), 0)

    def test_end_overrun_challenge(self):
        y = self.u[1:self.lo]
        res = mo.run_challenges(y, packetize(y), self.code, self.lo, len(self.u) + 500, tau=32, interior=1)
        self.assertEqual(res['end-overrun']['detected'], res['end-overrun']['planned'])


class Controls(unittest.TestCase):
    def test_uncut_control_counts_exactly_the_marked_frames(self):
        u = code_source(60_000)
        code = mo.SourceCode(u, 1)
        y = u[20_000:]
        out = detector.control_counts(y, packetize(y), code, [(25_001, 26_001), (1_001, 2_001)], near=20_000)
        self.assertEqual((out['startIndex'], out['tp'], out['fn'], out['fp'], out['unmeasured']), (20_000, 1000, 0, 0, 0))

    def test_summary_never_claims_absence_with_missing_takes(self):
        layout = dict(positions=[dict(mark='[0, 1) ms', planned=['straight', 'resume'], passed=False,
                                      takes={'straight': dict(markedFrames=0, markedSoundDetections=0, challengesPlanned=1,
                                                              challengesMissed=0, challengesRefused=0, sound=dict(alteredKept=0))},
                                      unmeasured='x')], controls={})
        s = detector.summarize(layout, 24)
        self.assertFalse(s['admitted'])
        self.assertIn('no confirmed leak in retained observations', s['statement'])

    def test_summary_admits_only_complete_clear_layouts_with_clean_controls(self):
        take = dict(markedFrames=0, markedSoundDetections=0, challengesPlanned=3, challengesMissed=0, challengesRefused=0,
                    sound=dict(alteredKept=0))
        pos = [dict(mark=str(i), planned=['straight'], passed=True, takes={'straight': take}, seekLandings=[]) for i in range(2)]
        control = dict(tp=5, fn=0, fp=0, tn=9, unmeasured=0)
        self.assertTrue(detector.summarize(dict(positions=pos, controls={'c': control}), 2)['admitted'])
        self.assertFalse(detector.summarize(dict(positions=pos, controls={'c': dict(unmeasured='x')}), 2)['admitted'])
        self.assertFalse(detector.summarize(dict(positions=pos, controls={'c': control | dict(fp=1)}), 2)['admitted'])
        self.assertFalse(detector.summarize(dict(positions=pos[:1], controls={'c': control}), 2)['admitted'])


if __name__ == '__main__':
    unittest.main()
