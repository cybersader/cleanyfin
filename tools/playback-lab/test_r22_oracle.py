import importlib.util
import unittest
import zlib
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from r22_oracle import (FINGERPRINT_ENERGY_LIMIT, RATE, SourceFingerprint, classify,
                        compare_transient, control_clock, observe, read_clocks, repeat_noise, silence,
                        transient_channels)


def good():
    return dict(markedFrames=0, markedSamples=0, audioIdentity='exact', extraZeroSamples=0, extraQuietSamples=0,
                transientMarginRms=0, pictureIntact=True, recoveryMs=100, staysInSync=True, lostSamples=1920)


class ViewerRuleTests(unittest.TestCase):
    def test_all_six_at_limits(self):
        result = classify(good())
        self.assertEqual(6, len(result))
        self.assertTrue(all(result.values()))

    def test_known_marked_frame_or_sample_overrides_everything(self):
        for key in ('markedFrames', 'markedSamples'):
            self.assertFalse(classify(good() | {key: 1})['marked-material'])

    def test_ambiguous_unproven_or_absent_identity_never_pass(self):
        for value in ('ambiguous', 'unproven', None):
            self.assertFalse(classify(good() | dict(audioIdentity=value))['marked-material'])

    def test_fingerprint_requires_positive_calibration_and_strict_floor(self):
        m = good() | dict(audioIdentity='fingerprint', markedFingerprintEnergy=FINGERPRINT_ENERGY_LIMIT/2)
        self.assertFalse(classify(m)['marked-material'])
        self.assertTrue(classify(m | dict(fingerprintPositiveControls=True))['marked-material'])
        self.assertFalse(classify(m | dict(fingerprintPositiveControls=True,
                                           markedFingerprintEnergy=FINGERPRINT_ENERGY_LIMIT))['marked-material'])

    def test_one_sample_longer_silence_fails_at_either_floor(self):
        for key in ('extraZeroSamples', 'extraQuietSamples'):
            self.assertFalse(classify(good() | {key: 1})['added-silence'])

    def test_transient_requires_nonnegative_calibrated_margin(self):
        self.assertTrue(classify(good() | dict(transientMarginRms=0))['added-pop'])
        self.assertFalse(classify(good() | dict(transientMarginRms=-.00001))['added-pop'])
        self.assertFalse(classify(good() | dict(transientMarginRms=float('nan')))['added-pop'])

    def test_missing_repeated_or_frozen_picture_fails(self):
        for value in (False, None):
            self.assertFalse(classify(good() | dict(pictureIntact=value))['kept-picture'])

    def test_sync_must_recover_by_100_ms_and_stay(self):
        self.assertFalse(classify(good() | dict(recoveryMs=100.001))['av-sync'])
        self.assertFalse(classify(good() | dict(staysInSync=False))['av-sync'])
        self.assertFalse(classify(good() | dict(recoveryMs=None))['av-sync'])

    def test_losing_one_more_than_40_ms_of_kept_sound_fails(self):
        self.assertFalse(classify(good() | dict(lostSamples=1921))['kept-sound-loss'])
        self.assertFalse(classify(good() | dict(lostSamples=None))['kept-sound-loss'])

    def test_missing_measurements_do_not_pass(self):
        self.assertFalse(any(classify({}).values()))


class SignalTests(unittest.TestCase):
    def test_silence_is_all_channels_not_any_one(self):
        x = np.array([[0,0], [1,1], [0,0], [0,33], [0,0]], dtype='<i2')
        self.assertEqual((1, 3), silence(x))

    def test_transient_detects_join_pop(self):
        n = np.arange(2000)
        tone = (21000*np.sin(2*np.pi*4200*n/RATE)).astype('<i2')
        reference = np.column_stack((tone, np.zeros_like(tone)))
        altered = reference.copy()
        altered[1000, 1] += 20000
        before = transient_channels(reference, 1000)
        after = transient_channels(altered, 1000)
        self.assertEqual(before.max(), after.max())  # The louder channel must not hide a pop.
        self.assertGreater((after-before).max(), .001)

    def test_steady_repeat_noise_and_same_picture_onset_are_distinct(self):
        source = np.arange(300, dtype='<i2')[:, None]
        delta = source.copy()
        delta[99] += 6
        self.assertEqual([0.], repeat_noise(source.tobytes(), source.tobytes(), 1))
        self.assertGreater(repeat_noise(source.tobytes(), delta.tobytes(), 1)[0], 0)
        a, ideal = np.array([.015625]), np.array([0.])
        self.assertLess(compare_transient(a, ideal, [0.])['marginRms'], 0)
        result = compare_transient(a, ideal, [0.], ([.015625], [0.]))
        self.assertEqual(0, result['marginRms'])
        self.assertEqual([.015625], result['uncutExcessRms'])
        self.assertLess(compare_transient(a + .0001, ideal, [0.], ([.015625], [0.]))['marginRms'], 0)

    def test_one_marked_sample_is_a_positive_detector_control(self):
        x = np.array([[0,10], [100,200], [500,700], [900,1200]], dtype='<i2')
        oracle = SourceFingerprint(x.tobytes(), 2, 0, 2, 3)
        count, score, calibrated = oracle.measure(x[2:3], 1)  # leaked vector at a nominally kept index
        self.assertEqual((0, 1., True), (count, score, calibrated))
        self.assertEqual((1, 0., True), oracle.measure(x[2:3], 2))
        self.assertLess(oracle.measure(x[1:2]+1, 1)[1], FINGERPRINT_ENERGY_LIMIT)

    def test_overlapping_kept_and_marked_fingerprints_cannot_clear_unknowns(self):
        x = np.array([[10,10], [10,10], [800,0]], dtype='<i2')
        oracle = SourceFingerprint(x.tobytes(), 2, 0, 1, 2)
        self.assertEqual((0, None, False), oracle.measure(x[:1]+1, 0))

    def test_audio_clock_requires_checksum_and_covers_leading_trim(self):
        x = np.arange(24, dtype='<i2').tobytes()
        def line(i, pts, b):
            return (f'Parsed_ashowinfo_0: n:{i} pts:{pts} pts_time:0 fmt:s16 channels:2 '
                    f'rate:48000 nb_samples:{len(b)//4} checksum:{zlib.adler32(b,0):08X}\n')
        trace = line(0, 100, x[:16]) + line(1, 104, x[16:])
        self.assertEqual([(101,0,3), (104,3,11)], read_clocks(trace, x[4:], 2)[0])
        self.assertEqual([], read_clocks(trace, x[:-4]+b'abcd', 2)[0])

    def test_uncut_control_sets_jitter_and_cannot_widen_past_cap(self):
        packets = [(i + (3 if i//100 % 2 else 1), i, i+100) for i in range(0, 3800, 100)]
        self.assertAlmostEqual(1+1/48, control_clock(packets, [0,40], 2, 0)['jitterMs'])
        self.assertIsNone(control_clock(packets, [0,90], 2, 0))

    def test_playback_clocks_are_not_wav_indices_and_a_late_drift_fails(self):
        # One dropped kept packet at the join, but the subsequent audio PTS still goes with the picture.
        rng = np.random.default_rng(11)
        x = rng.integers(-4000,4000, (RATE,2), dtype='<i2')
        a,b,one = 9600,15360,1920
        mark = SimpleNamespace(a=a, b=b, marked_frames={5,6,7}, join_seconds=Fraction(a,RATE),
                               frame_time=lambda f: Fraction(f,25) - (Fraction(b-a,RATE) if f >= 8 else 0))
        source = SourceFingerprint(x.tobytes(), 2, 0, a, b)
        packets = [(i if i < a else i-(b-a), k*one, (k+1)*one)
                   for k,i in enumerate([*range(0,a,one), *range(b+one,RATE,one)])]
        got = np.concatenate((x[:a], x[b+one:])).tobytes()
        ideal = np.concatenate((x[:a], x[b:])).tobytes()
        seq = [*range(5), *range(8,25)]
        v = [float(mark.frame_time(f))*1000 for f in seq]
        kw = dict(source=source, mark=mark, control=dict(videoJitterMs=0, audioBiasMs=0, jitterMs=1,
                                                         transientNoiseRms=[0., 0.]),
                  ideal=ideal, ideal_at=0, join=a, picture_ok=True, exact_issues=[])
        expected = observe(got, packets, v, seq, **kw)
        self.assertEqual(40, expected['keptSoundLossMs'])
        self.assertEqual(0, expected['worstAvOffsetMs'])
        self.assertTrue(classify(expected)['av-sync'])
        no_control = observe(got, packets, v, seq, fresh=True, **kw)
        self.assertIsNone(no_control['transientComparisons']['onset'])
        self.assertFalse(classify(no_control)['added-pop'])
        calibrated = observe(got, packets, v, seq, fresh=True, onset_control=([0., 0.], [0., 0.]), **kw)
        self.assertEqual({'onset', 'splice'}, set(calibrated['transientComparisons']))
        self.assertTrue(all(x is not None for x in calibrated['transientComparisons'].values()))
        shifted = packets[:-1] + [(packets[-1][0]+one, *packets[-1][1:])]
        self.assertFalse(classify(observe(got, shifted, v, seq, **kw))['av-sync'])


class CandidateRowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('format_matrix', Path(__file__).with_name('format-matrix.py'))
        cls.matrix = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.matrix)

    def run_entries(self):
        return {name: dict(summary=dict(result='r22-pass', deviationTypes=[], positions=dict(passed=24,total=24)),
                           result=dict(tuple=dict(source=name)))
                for name, spec in self.matrix.ff.LAYOUTS.items() if spec['audio'][0][0] in ('ac3','eac3')}

    def test_parts_of_a_cohort_or_an_unfinished_line_cannot_emit_experimental_rows(self):
        entries = self.run_entries()
        for finished in (False, True):
            if finished:
                entries.pop('mkv-h264-ac351')
            rows = self.matrix.candidate_rows(entries, 'receipt.json', '2026-11-25', finished)
            self.assertTrue(rows)
            self.assertEqual({'refused'}, {r['status'] for r in rows})

    def test_two_fixtures_sharing_tuple_both_have_to_pass(self):
        entries = self.run_entries()
        one, two = 'mkv-hevc10-eac351', 'mkv-hevc10-hdr10-eac351'
        entries[two]['result'] = entries[one]['result']
        rows = self.matrix.candidate_rows(entries, 'receipt.json', '2026-11-25', True)
        self.assertEqual(len(entries)-1, len(rows))
        self.assertEqual({'experimental'}, {r['status'] for r in rows})
        entries[two]['summary']['result'] = 'r22-fail'
        rows = self.matrix.candidate_rows(entries, 'receipt.json', '2026-11-25', True)
        self.assertEqual([one], [r['tuple']['source'] for r in rows if r['status'] == 'refused'])


if __name__ == '__main__':
    unittest.main()
