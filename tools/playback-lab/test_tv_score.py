"""Pure regressions for the TV lab's presentation oracles."""
from array import array
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import tv_score as score


def stereo(values):
    return array('h', (v for pair in values for v in pair))


def little_endian(pcm):
    if sys.byteorder != 'little':
        pcm = array('h', pcm)
        pcm.byteswap()
    return pcm.tobytes()


class TvScoreTests(unittest.TestCase):
    def test_frame_sequence_requires_all_frames_and_order(self):
        control = list(range(500))
        filtered = control[:137] + control[263:]
        self.assertTrue(score.frame_summary([None, 0] + control + [None])['controlComplete'])
        self.assertFalse(score.frame_summary(control[:2] + control[3:])['controlComplete'])
        result = score.frame_summary(filtered)
        self.assertTrue(result['filteredComplete'] and result['joinAdjacent'])
        self.assertEqual(result['marked'], 0)
        self.assertFalse(score.frame_summary(filtered[:137] + [None] + filtered[137:])['filteredComplete'])

    def test_stereo_diff_counts_position_and_energy(self):
        a = stereo([(800, 507), (1000, -1023), (1050, 1090)])
        b = stereo([(800, 507), (1002, -1020), (1050, 1095)])
        result = score.pcm_diff(a, b)
        self.assertEqual(result['differentSampleFrames'], 2)
        self.assertEqual(result['differentSamplesLR'], [1, 2])
        self.assertEqual((result['firstDifferentFrame'], result['lastDifferentFrame']), (1, 2))
        self.assertEqual(result['errorEnergyLSB2'], [4, 34])
        self.assertEqual(result['peakErrorLSB'], [2, 5])

    def test_repeat_floor_and_frozen_origin_across_the_splice(self):
        regions = {'prefix': (0, 4), 'suffix': (4, 8),
                   'joinBefore20ms': (2, 4), 'joinAfter20ms': (4, 6)}
        ctl = [(0, 0)] + [(800+i*100, -700+i*50) for i in range(11)]
        rep = list(ctl)
        rep[8] = (1502, -350)  # Two LSBs of repeat error, affecting one channel.
        cut = list(ctl[:4] + ctl[8:])
        cut[5] = cut[4]  # A local splice error must not trigger a suffix realignment.
        with tempfile.TemporaryDirectory() as temp, patch.multiple(
                score, TOTAL=12, START=4, END=8, KEPT=8, REGIONS=regions):
            def write(name, data, padding):
                p = Path(temp)/name
                p.write_bytes(little_endian(stereo([(0, 0)]*padding + data)))
                return p
            a = write('control', ctl, 3)
            b = write('repeat', rep, 7)
            c = write('filtered', cut, 5)
            source = little_endian(stereo([(0, 0)] + [(512, -512)]*11))
            noise = dict(control=0, repeatControl=0, filtered=0)
            floor = score.compare(a, b, None, source, noise)
            self.assertFalse(floor['valid'])
            self.assertEqual(floor['repeatability']['joinAfter20ms']['errorEnergyLSB2'], [4, 0])
            result = score.compare(a, b, c, source, noise)
        self.assertTrue(result['valid'])
        self.assertEqual(result['starts'], dict(control=3, repeatControl=7, filtered=5))
        self.assertTrue(result['controlVsFiltered']['prefix']['withinFloor'])
        self.assertFalse(result['controlVsFiltered']['suffix']['withinFloor'])
        self.assertEqual(result['controlVsFiltered']['joinAfter20ms']['firstDifferentFrame'], 1)


class VerdictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = spec_from_file_location('tv_harness', Path(__file__).with_name('tv-harness.py'))
        cls.harness = module_from_spec(spec)
        spec.loader.exec_module(cls.harness)

    def test_split_picture_requires_its_own_filtered_receipts(self):
        control = score.frame_summary(range(500))
        control.update(witnessSpanS=19.96, maxCaptureGapMs=20)
        filtered = score.frame_summary(list(range(137)) + list(range(263, 500)))
        filtered.update(witnessSpanS=14.92, maxCaptureGapMs=20)
        tap = dict(speed=1, runtimeVerified=True, displayTap='verified compositor')
        raw = dict(recordings=dict(controlDisplay=dict(tap, fixtureDurationS=20),
                                   filteredDisplay=dict(tap, fixtureDurationS=14.96),
                                   filtered=dict(grantRouteDeltas=[1, 1, 1])),
                   controlDisplayPicture=control, filteredDisplayPicture=filtered)
        results = self.harness.verdicts(raw, None)
        self.assertEqual(results['filtered handoff'][0], 'PASS')
        self.assertEqual(results['picture control'][0], 'PASS')
        self.assertEqual(results['kept picture at join'][0], 'not-measured')
        raw['recordings']['filteredDisplay']['grantRouteDeltas'] = [1, 1, 1]
        results = self.harness.verdicts(raw, None)
        self.assertEqual(results['kept picture at join'][0], 'PASS')
        self.assertEqual(results['marked picture'][0], 'PASS')

    def test_quarter_speed_picture_cannot_pass_real_speed_join(self):
        sampled = score.frame_summary([0, 1, 498, 499])
        sampled.update(witnessSpanS=14.92, maxCaptureGapMs=200)
        control = score.frame_summary(range(500))
        control.update(witnessSpanS=79.84, maxCaptureGapMs=50)
        filtered = score.frame_summary(list(range(137)) + list(range(263, 500)))
        filtered.update(witnessSpanS=59.68, maxCaptureGapMs=50)
        tap = dict(runtimeVerified=True, displayTap='verified compositor')
        raw = dict(recordings=dict(control=dict(tap, speed=1, fixtureDurationS=20),
                                   filtered=dict(tap, speed=1, fixtureDurationS=14.96, grantRouteDeltas=[1, 1, 1]),
                                   slowControl=dict(tap, speed=.25, fixtureDurationS=20),
                                   slowFiltered=dict(tap, speed=.25, fixtureDurationS=14.96, grantRouteDeltas=[1, 1, 1])),
                   controlPicture=sampled, filteredPicture=sampled,
                   slowControlPicture=control, slowFilteredPicture=filtered)
        results = self.harness.verdicts(raw, None)
        self.assertEqual(results['picture control'][0], 'PASS')
        self.assertIn('0.25x display', results['kept picture at join'][1])
        self.assertEqual(results['marked picture'][0], 'PASS')
        self.assertEqual(results['real-speed join'][0], 'not-measured')
        raw['slowControlPicture']['witnessSpanS'] = 19.96
        self.assertEqual(self.harness.verdicts(raw, None)['picture control'][0], 'FAIL')

    def test_calibrated_split_sound_reports_positive_leak(self):
        live = dict(runtimeVerified=True, activeQemuAudioRoute=[dict(sink='fixture', mute=False, corked=False)])
        tones = {'440': 5480, '660': 9480, '880': 5040}
        control = dict(tones=[tones, tones], capturePeakLSB=[10000, 10000], preRollRmsLSB=0)
        sound = dict(tones=[dict(tones, **{'880': 10}), dict(tones, **{'880': 5})])
        raw = dict(recordings=dict(control=live, repeatControl=live, filtered=dict(live, grantRouteDeltas=[1, 1, 1]),
                                   filteredDisplay=dict(grantRouteDeltas=[0, 0, 0])),
                   controlSound=control, repeatControlSound=control, filteredSound=sound)
        results = self.harness.verdicts(raw, None)
        self.assertEqual(results['sound control'][0], 'PASS')
        self.assertEqual(results['marked sound'][0], 'FAIL')
        self.assertEqual(results['kept PCM at join'][0], 'not-measured')


if __name__ == '__main__':
    unittest.main()
