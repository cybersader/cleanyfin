import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import format_measure as fm
import r22_oracle as r22
import r24_detector as detector


class PositiveControls(unittest.TestCase):
    def setUp(self):
        self.x = np.random.default_rng(504).integers(-10000, 10000, (1000, 2), dtype='<i2')
        self.ref = dict(pcm=self.x.tobytes(), channels=2)

    def take(self, y):
        return ([498, 499], y.tobytes(), ([(0, 0, len(y))], [19920, 19960]))

    def test_controls_require_position_not_only_length(self):
        good = self.x[100:].copy(); good[300, 1] += 3
        np.testing.assert_array_equal(detector.positive_slice(self.take(good), self.ref, 350, 500), good[250:400])
        for bad in (good[:-1], good[400:], np.roll(good, 2, axis=0)):
            with self.subTest(), self.assertRaises(fm.Unmeasured):
                detector.positive_slice(self.take(bad), self.ref, 350, 500)

    def test_duplicate_anchors_or_partial_packets_not_calibration(self):
        with self.assertRaises(fm.Unmeasured):
            detector.positive_slice(self.take(np.zeros_like(self.x)), {'pcm': bytes(self.x.nbytes), 'channels': 2}, 250, 500)
        take = self.take(self.x); take[2][0][0] = (0, 1, len(self.x))
        with self.assertRaises(fm.Unmeasured): detector.positive_slice(take, self.ref, 250, 500)

    def test_one_held_out_positive_outside_frozen_radius_refuses(self):
        x = self.x[250:500]; y = x.copy(); y[0, 0] += 2
        z = x.copy(); z[-1, 1] += 3
        with self.assertRaises(fm.Unmeasured): detector.Detector(x, y, z)
        z[-1, 1] -= 2
        d = detector.Detector(x, y, z)
        self.assertEqual(d.count(z), len(z)); self.assertEqual(d.residual_limit, 1)
        self.assertEqual(d.count(self.x[:200]), 0)

    def test_empty_is_not_a_control(self):
        with self.assertRaises(fm.Unmeasured): detector.Detector(*([self.x[:0]] * 3))


class TakeTests(unittest.TestCase):
    def setUp(self):
        self.x = np.random.default_rng(606).integers(-16000, 16000, (2048, 2), dtype='<i2')
        self.ref = dict(channels=2, pcm=self.x.tobytes(), lead=0, r22Clock={'audioBiasMs': 0})
        self.signature = r22.SourceFingerprint(self.ref['pcm'], 2, 0, 512, 600)
        self.mark = SimpleNamespace(a=512, b=600, marked_frames={1}, kept_frames=[0, 2], frame_time=lambda f: f/25)
        x = self.x[512:600]; self.det = detector.Detector(x, x+1, x-1)

    def score(self, y, seq=(0,2), packets=None):
        take = (seq, y.tobytes(), ([(0, 0, len(y))] if packets is None else packets, [f*40 for f in seq]))
        return detector.score(take, self.ref, self.mark, self.det, self.signature, 0)

    def test_safe_packet_and_calibrated_single_vector_change(self):
        y = self.x[:300].copy(); y[122] += 1
        r = self.score(y)
        self.assertEqual(r['residualSamples'], 1); self.assertEqual(r['markedSoundDetections'], 0)

    def test_a_single_marked_sample_or_frame_is_counted(self):
        y = self.x[:300].copy(); y[122] = self.x[555]+1
        r = self.score(y, (0,1,2)); self.assertEqual(r['markedFrames'], 1); self.assertEqual(r['residualMatches'], 1)
        self.assertEqual(self.score(self.x[300:600])['anchoredMarkedSamples'], 88)

    def test_exact_kept_vector_matching_the_positive_is_not_clear(self):
        self.x[122] = self.x[555]
        self.ref['pcm'] = self.x.tobytes()
        self.signature = r22.SourceFingerprint(self.ref['pcm'], 2, 0, 512, 600)
        result = self.score(self.x[:300])
        self.assertEqual(result['exactKeptCollisions'], 1)
        self.assertEqual(result['markedSoundDetections'], 1)

    def test_outside_calibration_or_missing_evidence_is_unmeasured(self):
        y = self.x[:300].copy(); y[122] += 2
        with self.assertRaises(fm.Unmeasured): self.score(y)
        for seq, packets in (((0,), None), ((0,2), []), ((0,2), [(0,0,200)]), ((0,2), [(0,1,300)])):
            with self.subTest(), self.assertRaises(fm.Unmeasured): self.score(self.x[:300], seq, packets)
        with patch.object(self.signature, 'locate', return_value=None), self.assertRaises(fm.Unmeasured):
            self.score(self.x[:300])
