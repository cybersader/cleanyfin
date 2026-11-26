import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, call

import drive_look as d


class DriveLookTest(unittest.TestCase):
    def test_dry_cli_is_offline_and_returns_private_png_pair(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(d, 'capture', side_effect=AssertionError), redirect_stdout(io.StringIO()) as output:
            d.main(['BACK', '--dry-run', '--settle', '0', '--out', folder])
            result = json.loads(output.getvalue())
            self.assertTrue(result['dry_run'])
            for name in ('webcam', 'screencap'):
                path = Path(result[name])
                self.assertEqual(path.read_bytes(), d.DryRun().grab())
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_timing_and_order_are_monotonic(self):
        events = []
        class TV:
            def send(self, action): events.append(action)
            def screen(self):
                events.append('screen')
                return d.DryRun().grab()
        class Cam:
            def grab(self):
                events.append('grab')
                return d.DryRun().grab()
        with tempfile.TemporaryDirectory() as folder:
            result = d.step('BACK', TV(), Cam(), Path(folder), 0.45,
                            clock=iter([0, .1, .55, 1.55, 2]).__next__, wait=events.append)
        self.assertEqual(events, ['BACK', .45, 'grab', 'screen'])
        self.assertEqual(result['timing_ms'], dict(action_round_trip=100, camera_call=1000,
                         action_to_webcam_return=1550, action_to_pair_return=2000))

    def test_bad_frames_leave_no_partial_result(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(d.DryRun, 'screen', return_value=b'invalid'):
            with self.assertRaisesRegex(ValueError, 'screencap'):
                d.step('BACK', d.DryRun(), d.DryRun(), Path(folder), 0)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_actions_are_targeted_and_fixed(self):
        target = '192.0.2.1:30000'
        with patch.object(d, 'capture') as run:
            tv = d.WirelessTV(target)
            tv.send('DPAD_UP'); tv.send('SETTINGS'); tv.screen()
            with self.assertRaises(ValueError): tv.send('POWER')
        prefix = ['adb', '-s', target]
        self.assertEqual(run.call_args_list, [call(prefix + args) for args in [
            ['shell', 'input', 'keyevent', 'KEYCODE_DPAD_UP'],
            ['shell', 'am', 'start', '-a', 'android.settings.SETTINGS'], ['exec-out', 'screencap', '-p']]])

    def test_reject_unscoped_target_and_invalid_crop(self):
        for target in ('', 'device', '192.0.2.1:5555', '192.0.2.1:05555', '192.0.2.1:65536'):
            with self.subTest(target=target), self.assertRaises(argparse.ArgumentTypeError): d.endpoint(target)
        for roi in ('0,0,0,4', '1920,4,1,1', '-1,4,1,4'):
            with self.subTest(roi=roi), self.assertRaises(argparse.ArgumentTypeError): d.crop(roi)
        self.assertEqual(d.crop('10,15,640,480'), (10, 15, 640, 480))

    def test_invalid_step_does_not_send(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(d.DryRun, 'send') as send:
            for action, settle in [('POWER', 0), ('HOME', -1), ('BACK', float('nan'))]:
                with self.assertRaises(ValueError):
                    d.step(action, d.DryRun(), d.DryRun(), Path(folder), settle)
            send.assert_not_called()

    def test_camera_is_a_single_cropped_silent_grab(self):
        with patch.object(d, 'capture', return_value=b'pixels') as run:
            self.assertEqual(d.Link2('/dev/video-test', (5, 6, 100, 200)).grab(), b'pixels')
        cmd, = run.call_args.args
        self.assertEqual(cmd[cmd.index('-frames:v')+1], '1')
        self.assertEqual(cmd[cmd.index('-vf')+1], 'crop=100:200:5:6:exact=1')
        self.assertIn('-an', cmd)
        self.assertEqual(cmd[-1], 'pipe:1')


if __name__ == '__main__':
    unittest.main()
