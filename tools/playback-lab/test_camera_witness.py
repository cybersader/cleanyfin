"""Synthetic optical fixture and fake UVC controls; never opens physical devices."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from camera_picture import CalibrationError, calibrate_camera, ff, read_cells, write_calibration_clip
from camera_uvc import aim_camera, locked_settings, parse_controls, preflight, select_microphone
from camera_witness import _tone_report, score_camera, summarize_picture
from hard_fixture_lab import BITS, SYNC

HERE = Path(__file__).resolve().parent


class PictureMathTests(unittest.TestCase):
    def setUp(self):
        bits = (*SYNC, *((137 >> i) & 1 for i in range(BITS)))
        self.cells = np.repeat(np.array([bits, tuple(1-b for b in bits)])[:, :, None], 25, axis=2)*220+16

    def test_identifies_marked_id(self):
        self.assertEqual(read_cells(self.cells)[0], 137)

    def test_refuses_blended_and_spatially_split_bits(self):
        half = self.cells.copy()
        half[:, -1, :] = 125
        self.assertIsNone(read_cells(half))
        split = self.cells.copy()
        split[:, -1, :5] = 252-split[:, -1, :5]
        self.assertIsNone(read_cells(split))

    def test_sync_must_match_actual_word_not_only_its_complement(self):
        self.cells[:, 0, :] = 252-self.cells[:, 0, :]
        self.assertIsNone(read_cells(self.cells))

    def test_decode_gap_not_capture_rate_bounds_hidden_dwells(self):
        out = summarize_picture([0, None, None, 3], [F(i, 30) for i in range(4)], total=4, start=1, end=3)
        self.assertEqual(out['largestGapMs'], 100)
        self.assertEqual(out['largestRawCameraGapMs'], 33.334)
        self.assertEqual(out['coverageBound']['maxWholeNominalDwellsPerBlindInterval'], 2)
        self.assertEqual(out['coverageBound']['observedCameraFps'], 30)
        self.assertEqual(out['keptFrameCoverage'], 1)

    def test_exact_source_frame_duration_is_not_rounded_out(self):
        out = summarize_picture([0, 2], [F(0), F(1, 25)], total=3, start=1, end=2)
        self.assertEqual(out['coverageBound']['maxWholeNominalDwellsPerBlindInterval'], 1)
        self.assertEqual(out['joinGapMs'], 40)

    def test_positive_is_reported_without_endpoints(self):
        out = summarize_picture([None, 1], [F(0), F(1, 25)], total=3, start=1, end=2)
        self.assertEqual(out['outcome'], 'marked_seen')
        self.assertFalse(out['sourceEndpointsSeen'])
        self.assertIsNone(out['largestGapMs'])

    def test_unbracketed_or_reordered_absence_is_inconclusive(self):
        for ids in ((None, 2, 3), (0, 3, 2, 3)):
            out = summarize_picture(list(ids), [F(i, 30) for i in range(len(ids))], total=4, start=1, end=2)
            self.assertEqual(out['outcome'], 'inconclusive')


class FakeControls:
    def __init__(self, values):
        self.values = deepcopy(values)
        self.writes = []

    def read(self):
        return deepcopy(self.values)

    def set(self, key, value):
        c = self.values[key]
        if 'min' in c and not (c['min'] <= value <= c['max'] and not (value-c['min']) % c['step']):
            raise AssertionError(f'Out of range or off step: {key}={value}')
        c['value'] = value
        self.writes.append((key, value))


class ControlTests(unittest.TestCase):
    def test_parse_controls_retains_writable_inactive_manual_values(self):
        text = '''pan_absolute 0x009a0908 (int) : min=-36000 max=36000 step=3600 default=0 value=-3600
        exposure_absolute 0x009a0902 (int) : min=5 max=500 step=1 value=45 flags=inactive
        button 0x009a091a (button) : flags=write-only
        status 0x009a091b (int) : min=0 max=10 step=1 value=4 flags=read-only'''
        self.assertEqual(parse_controls(text), {'pan_absolute': dict(min=-36000, max=36000, step=3600, value=-3600),
                                               'exposure_absolute': dict(min=5, max=500, step=1, value=45)})

    def test_aim_nudges_and_restores_exposure_and_ptz(self):
        initial = dict(auto_exposure=dict(value=3), exposure_time_absolute=dict(value=92),
                       exposure_dynamic_framerate=dict(value=1), focus_automatic_continuous=dict(value=1),
                       focus_absolute=dict(value=30), white_balance_automatic=dict(value=1),
                       white_balance_temperature=dict(value=4500),
                       pan_absolute=dict(min=-36000, max=36000, step=3600, value=0),
                       tilt_absolute=dict(min=-36000, max=36000, step=3600, value=0),
                       zoom_absolute=dict(min=100, max=200, step=2, value=140))
        control, report = FakeControls(initial), {}
        def calibrate():
            self.assertEqual(control.values['auto_exposure']['value'], 1)
            self.assertEqual(control.values['exposure_dynamic_framerate']['value'], 0)
            self.assertEqual(control.values['focus_automatic_continuous']['value'], 0)
            self.assertEqual(control.values['white_balance_automatic']['value'], 0)
            if control.values['tilt_absolute']['value'] != -3600 or control.values['zoom_absolute']['value'] != 100:
                raise CalibrationError('Clipped screen')
            return 'good quad'
        with locked_settings(control, report) as settings:
            self.assertEqual(aim_camera(settings, calibrate, enabled=True), 'good quad')
            self.assertEqual(report['aiming']['final'], dict(pan_absolute=0, tilt_absolute=-3600, zoom_absolute=100))
            self.assertGreater(len(report['aiming']['attempts']), 2)
        self.assertEqual(control.values, initial)
        self.assertTrue(all(item['restored'] for item in report['restore'].values()))

    def test_aim_unsupported_reports_manual_and_never_moves(self):
        report, control = {}, FakeControls({})
        with locked_settings(control, report) as settings:
            aim_camera(settings, lambda: 'good quad', enabled=True)
        self.assertIn('aim manually', report['aiming']['note'])
        self.assertEqual(control.writes, [])

    def test_aim_restores_after_all_bounded_attempts_fail(self):
        initial = dict(zoom_absolute=dict(min=100, max=200, step=1, value=140))
        report, control = {}, FakeControls(initial)
        def fail():
            raise CalibrationError('Clipped screen')
        with self.assertRaises(CalibrationError), locked_settings(control, report) as settings:
            aim_camera(settings, fail, enabled=True)
        self.assertLessEqual(len(report['aiming']['attempts']), 8)
        self.assertEqual(control.values, initial)

    def test_preflight_fake_usb_mode_selection_and_own_mic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            usb = root/'sys/devices/usb1/1-1'
            interface = usb/'1-1:1.0'
            interface.mkdir(parents=True)
            for key, value in (('idVendor', '1111'), ('idProduct', '2222'), ('product', 'Insta360 Link')):
                (usb/key).write_text(value)
            (interface/'driver').symlink_to(root/'sys/bus/usb/drivers/uvcvideo')
            video = root/'sys/class/video4linux/video27'
            video.mkdir(parents=True)
            (video/'device').symlink_to(interface)
            card = root/'sys/class/sound/card4'
            card.mkdir(parents=True)
            (card/'device').symlink_to(interface)
            endpoint = root/'proc/asound/card4/pcm2c'
            endpoint.mkdir(parents=True)
            (endpoint.parent/'id').write_text('Link')
            modes = """[0]: 'MJPG' (Motion-JPEG, compressed)
                Size: Discrete 1920x1080
                  Interval: Discrete 0.033s (30.000 fps)
                Size: Discrete 1280x720
                  Interval: Discrete 0.017s (60.000 fps)
                Size: Discrete 640x480
                  Interval: Discrete 0.004s (240.000 fps)"""
            def fake_query(args):
                self.assertEqual(args, ['v4l2-ctl', '-d', '/dev/video27', '--list-formats-ext'])
                return modes
            card = preflight('insta360', root=root, query=fake_query)['selected']
            self.assertEqual(card['chosenMode']['fps'], 60)
            self.assertEqual(card['chosenMode']['height'], 720)
            self.assertEqual(select_microphone(card, None)['alsa'], 'plughw:CARD=Link,DEV=2')
            with self.assertRaises(ValueError):
                select_microphone(card, 4)


# FFmpeg takes the real fixture barcode generator, integrates a short exposure
# on a high-rate sample grid, then applies lens/optical and recording defects.
OPTICS = ('scale=301:169:flags=bicubic,scale=320:180:flags=neighbor,pad=400:240:40:30:color=0x202020,'
          'perspective=x0=16:y0=3:x1=391:y1=12:x2=3:y2=229:x3=384:y3=237:sense=destination,'
          'gblur=sigma=0.9,noise=alls=5:all_seed=109:allf=t+u')


def encode(*args):
    ff('ffmpeg', '-n', '-filter_threads', '2', '-filter_complex_threads', '2', *args)


class SyntheticCameraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix='camera-witness-')
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        cal = cls.root/'cal.mp4'
        write_calibration_clip(cal)
        cls.cal = cls.root/'cal.mkv'
        encode('-i', cal, '-vf', OPTICS, '-t', '1.75', '-c:v', 'ffv1', '-threads:v', '2', cls.cal)
        cls.source = cls.root/'source.mkv'
        # Generate the original barcode and three matching marked/kept tones.
        fixture = runpy.run_path(str(HERE/'hard-fixture.py'))
        tone = "aevalsrc='0.19*sin(2*PI*if(lt(t,5.48),440*t,if(lt(t,10.52),880*t,660*t)))':s=8000:d=20"
        encode('-f', 'lavfi', '-i', fixture['barcode_filter'](), '-f', 'lavfi', '-i', tone,
               '-vf', 'scale=320:180', '-c:v', 'ffv1', '-threads:v', '2', '-c:a', 'flac', cls.source)
        cls.control = cls.capture('control60.mkv', 60)
        cls.filtered = cls.capture('filtered60.mkv', 60, omit=True)
        cls.slow = cls.capture('filtered30.mkv', 30, omit=True)

    @classmethod
    def capture(cls, name, fps, *, omit=False):
        output = cls.root/name
        # Integrate ~13 ms of rolling display changes in each shutter window.
        timeline = ('fps=300,tmix=frames=5,'+f'fps={fps},'+OPTICS)
        if omit:
            chain = ("[0:v]select='not(between(n,137,262))',setpts=N/(25*TB),"+timeline+"[v];"
                     '[0:a]asplit=2[aa][ab];[aa]atrim=end=5.48,asetpts=PTS-STARTPTS[a];'
                     '[ab]atrim=start=10.52,asetpts=PTS-STARTPTS[b];[a][b]concat=v=0:a=1:n=2[out]')
            encode('-i', cls.source, '-filter_complex', chain, '-map', '[v]', '-map', '[out]',
                   '-c:v', 'ffv1', '-threads:v', '2', '-c:a', 'flac', output)
        else:
            encode('-i', cls.source, '-vf', timeline, '-c:v', 'ffv1', '-threads:v', '2', '-c:a', 'flac', output)
        return output

    def test_warped_calibration_clears_both_phases(self):
        _, out = calibrate_camera(self.cal)
        self.assertTrue(all(count >= 2 for count in out['phaseReadings'].values()))
        self.assertGreaterEqual(out['minBitMargin'], out['requiredBitMargin'])
        self.assertEqual(len(out['screenQuadPx']), 4)
        self.assertGreater(out['screenQuadPx'][1][1]-out['screenQuadPx'][0][1], 4)

    def test_refuses_missing_corner_and_marginal_cells(self):
        clipped = self.root/'clipped.mkv'
        stuck = self.root/'stuck.mkv'
        encode('-i', self.cal, '-vf', 'drawbox=x=245:y=0:w=155:h=75:color=black:t=fill', '-c:v', 'ffv1', clipped)
        encode('-i', self.cal, '-vf', 'drawbox=x=190:y=100:w=115:h=40:color=gray:t=fill', '-c:v', 'ffv1', stuck)
        for broken in (clipped, stuck):
            with self.subTest(video=broken.name), self.assertRaises(CalibrationError):
                calibrate_camera(broken)

    def test_known_marked_take_and_tone_detected(self):
        out = score_camera(self.control, self.cal, uncut_video=self.control)
        self.assertEqual(out['evidence'], 'screen camera witness')
        self.assertEqual(out['picture']['markedFramesSeen'], list(range(137, 263)))
        self.assertEqual(out['picture']['outcome'], 'marked_seen')
        self.assertTrue(out['uncutPictureControl']['allSourceFramesSeen'])
        self.assertEqual(out['tone']['outcome'], 'tone_observed')
        self.assertGreater(out['picture']['unreadableCameraFrames'], 0)

    def test_clean_60_fps_covers_all_kept_and_tone_control_passes(self):
        out = score_camera(self.filtered, self.cal, uncut_video=self.control)
        self.assertEqual(out['picture']['outcome'], 'bounded_no_marked_seen')
        self.assertEqual(out['picture']['markedFramesSeen'], [])
        self.assertEqual(out['picture']['keptFrameCoverage'], 1)
        self.assertEqual(out['tone']['outcome'], 'tone_not_observed_above_threshold')

    def test_30_fps_blended_recording_has_real_coverage_holes(self):
        out = score_camera(self.slow, self.cal)['picture']
        self.assertGreater(out['unreadableCameraFrames'], 0)
        self.assertLess(out['keptFrameCoverage'], 1)
        self.assertGreaterEqual(out['largestGapMs'], 66)
        self.assertGreaterEqual(out['coverageBound']['maxWholeNominalDwellsPerBlindInterval'], 1)

    def test_tone_refuses_without_valid_uncut_control(self):
        self.assertEqual(_tone_report(self.filtered, None)['outcome'], 'uncontrolled')
        self.assertEqual(_tone_report(self.filtered, self.filtered)['outcome'], 'uncontrolled')

    def test_cli_isolated_dry_run(self):
        result = subprocess.run([sys.executable, '-I', HERE/'camera_witness.py', '--dry-run', self.filtered,
                                 '--calibration', self.cal], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['evidence'], 'screen camera witness')


if __name__ == '__main__':
    unittest.main()
