import math
import unittest

import numpy as np

from r25_echo import ALIAS_WEIGHT, BLOCK, CODE_CHANNELS, SELF_WEIGHT, levels, project


class EchoProjectionTests(unittest.TestCase):
    def setUp(self):
        self.source = np.random.default_rng(25).uniform(-.2, .2, (3 * BLOCK, 6))
        self.kept = self.source[128:256] * 32768
        self.p = np.arange(128, 256)
        self.alias = ALIAS_WEIGHT[self.p, None] * self.source[255 - self.p]
        self.self_term = SELF_WEIGHT[self.p, None] * self.source[self.p]

    def test_recovers_signed_echo_and_kept_attenuation(self):
        gain = np.array([.2, -.8, .3, .5, .9, -.4])
        marked = (255 - self.p) >= 36
        total = self.self_term * -.7 + self.alias * np.where(marked[:, None], gain, -.5)
        got = project(self.source, self.p, total * 32768, self.kept, 36, 128)
        np.testing.assert_array_equal(got.positions, self.p[marked])
        np.testing.assert_allclose(got.echo, (self.alias * gain)[marked][:, CODE_CHANNELS], atol=1e-14)
        self.assertTrue(all(not row['belowLimits'] for row in got.rows()))

    def test_kept_attenuation_alone_is_not_marked(self):
        got = project(self.source, self.p, self.self_term * 32768, self.kept, 0, 128)
        self.assertLess(np.max(np.abs(got.echo)), 1e-14)
        self.assertTrue(all(row['belowLimits'] for row in got.rows()))

    def test_no_marked_support_in_remote_half_block(self):
        p = self.p + BLOCK
        self.assertIsNone(project(self.source, p, self.kept, self.kept, 0, 128))

    def test_mark_is_half_open_and_echo_window_does_not_dilute_rms(self):
        got = project(self.source, self.p, self.alias * 32768, self.kept, 40, 60)
        np.testing.assert_array_equal(got.positions, np.arange(196, 216))
        e = got.echo[:, 0]
        self.assertAlmostEqual(got.rows()[0]['rmsDbfs'], 20 * math.log10(np.sqrt(np.mean(e ** 2))))

    def test_rejects_unidentifiable_code(self):
        source = self.source.copy()
        source[:128, 1] = 0.
        with self.assertRaisesRegex(ValueError, 'not identifiable'):
            project(source, self.p, self.alias * 32768, self.kept, 0, 128)

    def test_requires_absolute_and_relative_peak_and_rms(self):
        self.assertTrue(levels([1e-6, 0], [.1, .1])['belowLimits'])
        self.assertFalse(levels([1e-4, 0], [.8, .8])['belowLimits'])  # absolute only fails
        self.assertFalse(levels([1e-6, 0], [1e-5, 1e-5])['belowLimits'])  # relative only fails
        self.assertFalse(levels([1e-7, 0], [0., 0.])['belowLimits'])
        self.assertTrue(levels([0., 0.], [0., 0.])['belowLimits'])
        row = levels([.01, 0], [.1, .1])
        self.assertAlmostEqual(row['peakDbfs'], -40.)
        self.assertAlmostEqual(row['rmsDbfs'], -43.0102999566)
        self.assertAlmostEqual(row['peakRelativeDb'], -20.)
        self.assertAlmostEqual(row['rmsRelativeDb'], -23.0102999566)


if __name__ == '__main__':
    unittest.main()
