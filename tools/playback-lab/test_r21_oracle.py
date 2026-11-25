import struct
import unittest

from r21_oracle import classify_pcm


def pcm(*n):
    return struct.pack(f'<{len(n)}h', *n)


class R21OracleTests(unittest.TestCase):
    def score(self, actual, expected=(10, 11, 12, 13), **options):
        for key in ('preceding', 'marked_before', 'marked_after'):
            if key in options:
                options[key] = pcm(*options[key])
        return classify_pcm(pcm(*expected), pcm(*actual), size=2, **options)

    def test_exact_straight_cut_and_fresh_start(self):
        self.assertEqual(([], True), self.score((10, 11, 12, 13)))
        self.assertEqual(([], True), self.score((10, 11, 12, 13), fresh=True, original_lead=0))

    def test_only_one_extra_kept_before_start(self):
        issues, passed = self.score((9, 10, 11, 12, 13), preceding=(8, 9), marked_before=(20, 21),
                                    fresh=True, original_lead=0)
        self.assertEqual([dict(kind='extra-kept-sample', count=1, side='before-start', presented=1, original=0)], issues)
        self.assertTrue(passed)
        self.assertFalse(self.score((8, 9, 10, 11, 12, 13), preceding=(8, 9), fresh=True, original_lead=0)[1])
        self.assertFalse(self.score((9, 10, 11, 12, 13), preceding=(8, 9), fresh=False, original_lead=0)[1])
        self.assertFalse(self.score((9, 10, 11, 12, 13), preceding=(8, 9), fresh=True)[1])

    def test_compare_against_independent_original_lead(self):
        issues, passed = self.score((8, 9, 10, 11, 12, 13), preceding=(8, 9), fresh=True, original_lead=1)
        self.assertTrue(passed)
        self.assertEqual(1, issues[0]['count'])
        self.assertEqual(2, issues[0]['presented'])
        self.assertEqual(([], True), self.score((9, 10, 11, 12, 13), preceding=(9,), fresh=True, original_lead=1))
        issues, passed = self.score((10, 11, 12, 13), fresh=True, original_lead=1)
        self.assertFalse(passed)
        self.assertEqual([dict(kind='kept-sample-lost', count=1, side='before-start')], issues)

    def test_marked_sample_is_never_allowed(self):
        issues, passed = self.score((21, 10, 11, 12, 13), preceding=(9,), marked_before=(20, 21), fresh=True,
                                    original_lead=0)
        self.assertFalse(passed)
        self.assertEqual([dict(kind='marked-sample-presented', count=1, side='before-start')], issues)
        issues, passed = self.score((10, 11, 21, 12, 13), marked_before=(20, 21), join=2)
        self.assertFalse(passed)
        self.assertEqual([dict(kind='marked-sample-presented', count=1, side='join', sample=2)], issues)
        issues, passed = self.score((10, 11, 21, 13), marked_before=(20, 21), join=2)
        self.assertFalse(passed)
        self.assertEqual(['marked-sample-presented', 'kept-sample-lost'], [i['kind'] for i in issues])

    def test_a_periodic_alias_or_unrecognized_audio_never_passes(self):
        for marked, kind in [((8, 9), 'audio-identity-ambiguous'), ((), 'audio-identity-unproven')]:
            issues, passed = self.score((9, 10, 11, 12, 13), preceding=(9,) if marked else (8,), marked_before=marked,
                                        fresh=True, original_lead=0)
            self.assertFalse(passed)
            self.assertEqual(kind, issues[0]['kind'])

    def test_right_side_kept_duplicate_is_not_a_fresh_start_exception(self):
        issues, passed = self.score((10, 11, 11, 12, 13), fresh=True, original_lead=0)
        self.assertFalse(passed)
        self.assertEqual([dict(kind='extra-kept-sample', count=1, side='at-or-after-start', sample=2)], issues)

    def test_removed_altered_and_shifted_audio_remain_distinct(self):
        for inputs, kind in [((10, 12, 13), 'kept-sample-lost'), ((10, 11, 42, 13), 'kept-sample-altered')]:
            issues, passed = self.score(inputs, fresh=True, original_lead=0)
            self.assertFalse(passed)
            self.assertEqual(kind, issues[0]['kind'])
        issues, passed = self.score(range(11, 61), expected=range(10, 60))
        self.assertFalse(passed)
        self.assertEqual([dict(kind='kept-sample-shifted', count=1, side='at-or-after-start', sample=0)], issues)

    def test_incomplete_interleaved_sample_is_invalid(self):
        with self.assertRaises(ValueError):
            classify_pcm(pcm(1), b'\0', size=2)


if __name__ == '__main__':
    unittest.main()
