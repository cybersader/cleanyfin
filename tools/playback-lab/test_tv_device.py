"""Pure regressions for the TV harness's real-box mode: no device, adapter or network."""
import os
from pathlib import Path
import tempfile
import unittest

import tv_device as dev

V4L2_CTL = """ioctl: VIDIOC_ENUM_FMT
\tType: Video Capture

\t[0]: 'MJPG' (Motion-JPEG, compressed)
\t\tSize: Discrete 1920x1080
\t\t\tInterval: Discrete 0.017s (60.000 fps)
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t\tSize: Discrete 1280x720
\t\t\tInterval: Discrete 0.017s (60.000 fps)
\t[1]: 'YUYV' (YUYV 4:2:2)
\t\tSize: Discrete 1920x1080
\t\t\tInterval: Discrete 0.017s (60.000 fps)
\t\t\tInterval: Discrete 0.033s (30.000 fps)
\t[2]: 'NV12' (Y/UV 4:2:0)
\t\tSize: Discrete 1920x1080
\t\t\tInterval: Discrete 0.033s (30.000 fps)
"""

USB2_V4L2_CTL = """\t[0]: 'MJPG' (Motion-JPEG, compressed)
\t\tSize: Discrete 1920x1080
\t\t\tInterval: Discrete 0.017s (60.000 fps)
\t[1]: 'YUYV' (YUYV 4:2:2)
\t\tSize: Discrete 1920x1080
\t\t\tInterval: Discrete 0.200s (5.000 fps)
"""

FFMPEG_LIST = """[video4linux2,v4l2 @ 0x5581] Compressed:       mjpeg :          Motion-JPEG : 1920x1080 1600x1200 1280x720
[video4linux2,v4l2 @ 0x5581] Raw       :     yuyv422 :           YUYV 4:2:2 : 1920x1080 1280x720
/dev/video2: Immediate exit requested
"""

ARECORD = """**** List of CAPTURE Hardware Devices ****
card 3: MV7 [Shure MV7], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 4: MS2130 [USB3 Video], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
"""

CARDS = """ 1 [PCH            ]: HDA-Intel - HDA Intel PCH
                      HDA Intel PCH at 0x4206220000 irq 167
 3 [MV7            ]: USB-Audio - Shure MV7
                      Shure Inc Shure MV7 at usb-0000:00:14.0-11.1, full speed
 4 [MS2130         ]: USB-Audio - USB3 Video
                      MACROSILICON USB3 Video at usb-0000:00:14.0-2, super speed
"""

ONN = {'ro.product.manufacturer': 'onn', 'ro.product.brand': 'onn', 'ro.product.model': 'onn. 4K Pro Streaming Device',
       'ro.product.device': 'tvpro', 'ro.build.version.release': '14', 'ro.build.version.sdk': '34',
       'ro.build.fingerprint': 'onn/tvpro/tvpro:14/UTT3.240625.001/1:user/release-keys',
       'ro.product.cpu.abi': 'arm64-v8a', 'ro.product.cpu.abilist':'arm64-v8a,armeabi-v7a', 'ro.build.characteristics': 'tv'}
TV_FEATURES = 'feature:android.software.leanback\nfeature:android.hardware.hdmi.cec\n'
GTV = {'com.google.android.apps.tv.launcherx': '1', 'com.netflix.ninja': '9'}


def manifest(**over):
    base = dict(packages={'com.netflix.ninja': '9', 'org.jellyfin.androidtv': '120'},
                settings={'global/encoded_surround_output': None, 'secure/match_content_frame_rate': '1',
                          'global/adb_wifi_enabled': '1'},
                musicVolume={'value': 7, 'min': 0, 'max': 15}, wholphinVersion=None, labScratch=[])
    base.update(over)
    return base


class DiscoveryParsing(unittest.TestCase):
    def test_v4l2_ctl_prefers_uncompressed_1080p60(self):
        modes = dev.parse_v4l2_ctl(V4L2_CTL)
        self.assertEqual(len(modes), 4)
        self.assertEqual(modes[0], dict(fourcc='MJPG', compressed=True, width=1920, height=1080, fps=[60.0, 30.0]))
        mode, why = dev.choose_mode(modes)
        self.assertEqual((mode['fourcc'], mode['compressed']), ('YUYV', False))
        self.assertIn('uncompressed', why)

    def test_usb2_class_falls_back_to_mjpeg_only_at_60(self):
        mode, why = dev.choose_mode(dev.parse_v4l2_ctl(USB2_V4L2_CTL))
        self.assertEqual(mode['fourcc'], 'MJPG')
        self.assertIn('Motion-JPEG', why)

    def test_ffmpeg_listing_has_no_rates_and_is_verified_later(self):
        modes = dev.parse_ffmpeg_formats(FFMPEG_LIST)
        self.assertIn(dict(fourcc='YUYV', compressed=False, width=1920, height=1080, fps=[]), modes)
        mode, why = dev.choose_mode(modes)
        self.assertEqual(mode['fourcc'], 'YUYV')
        self.assertIn('verified from the capture', why)

    def test_no_1080_mode_is_refused(self):
        self.assertIsNone(dev.choose_mode([dict(fourcc='YUYV', compressed=False, width=1280, height=720, fps=[60.0])])[0])

    def test_arecord_and_proc_cards(self):
        self.assertEqual(dev.parse_arecord_l(ARECORD)[1], dict(card=4, id='MS2130', name='USB3 Video', device=0))
        cards = dev.parse_asound_cards(CARDS)
        self.assertEqual([c['card'] for c in cards], [1, 3, 4])
        self.assertEqual(cards[2]['id'], 'MS2130')

    def fake_tree(self, root):
        """sysfs/procfs with a webcam, a USB microphone (desktop default) and an MS2130."""
        def usb(name, vid, pid, product):
            d = root/'sys/devices/pci0000:00/usb1'/name
            d.mkdir(parents=True)
            (d/'idVendor').write_text(vid+'\n'); (d/'idProduct').write_text(pid+'\n'); (d/'product').write_text(product+'\n')
            (d/(name+':1.0')).mkdir()
            return d/(name+':1.0')
        def video(n, iface, label, index='0'):
            v = root/'sys/class/video4linux'/f'video{n}'
            v.mkdir(parents=True)
            (v/'name').write_text(label+'\n'); (v/'index').write_text(index+'\n')
            if iface is not None:
                os.symlink(iface, v/'device')
        def card(n, iface, ident, capture=True):
            c = root/'sys/class/sound'/f'card{n}'
            c.mkdir(parents=True)
            os.symlink(iface, c/'device')
            p = root/'proc/asound'/f'card{n}'
            p.mkdir(parents=True)
            (p/'id').write_text(ident+'\n')
            if capture:
                (p/'pcm0c').mkdir()
        cam = usb('1-3', '046d', '085b', 'Logitech Webcam C925e')
        mic = usb('1-11.1', '14ed', '1012', 'Shure MV7')
        hdmi = usb('2-2', '345f', '2130', 'USB3 Video')
        video(0, cam, 'Logitech Webcam C925e'); video(1, cam, 'Logitech Webcam C925e', '1')
        video(2, hdmi, 'USB3 Video: USB3 Video'); video(3, hdmi, 'USB3 Video: USB3 Video', '1')
        video(9, None, 'OBS Virtual Camera')
        card(1, cam, 'C925e'); card(3, mic, 'MV7'); card(4, hdmi, 'MS2130')

    def test_discovery_pairs_adapter_video_and_audio_and_refuses_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fake_tree(root)
            found = dev.discover(root, default_source_card=3)
        self.assertEqual(len(found['adapters']), 1)
        a = found['adapters'][0]
        self.assertEqual((a['video'], a['card'], a['cardId'], a['usb']), ('/dev/video2', 4, 'MS2130', '345f:2130'))
        reasons = {r['device'].split(' [')[0]: r['reason'] for r in found['refused']}
        self.assertTrue(reasons['Logitech Webcam C925e'].startswith('webcam'))
        self.assertTrue(reasons['Shure MV7'].startswith('not a known HDMI capture adapter'))
        self.assertIn('virtual', reasons['/dev/video9 OBS Virtual Camera'])

    def test_desktop_default_source_is_never_the_adapter_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fake_tree(root)
            found = dev.discover(root, default_source_card=4)
        self.assertEqual(found['adapters'], [])
        self.assertTrue(any('own audio capture card' in r['reason'] for r in found['refused']))

    def test_owner_named_usb_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fake_tree(root)
            found = dev.discover(root, extra_ids=['046D:085B'])
        self.assertEqual({a['usb'] for a in found['adapters']}, {'345f:2130', '046d:085b'})

    def test_default_source_card_from_pactl(self):
        sources = '[{"name":"alsa_input.usb-Shure","properties":{"alsa.card":"3"}},{"name":"x","properties":{}}]'
        self.assertEqual(dev.default_source_card('alsa_input.usb-Shure', sources), 3)
        self.assertIsNone(dev.default_source_card('missing', sources))


class Identity(unittest.TestCase):
    def test_accepts_only_the_named_google_tv_box(self):
        ok, found, why = dev.identity(ONN, TV_FEATURES, GTV)
        self.assertTrue(ok, why)
        self.assertEqual(found['model'], 'onn. 4K Pro Streaming Device')

    def test_32bit_userspace_selects_32bit_release_on_64bit_soc(self):
        ok,found,_=dev.identity(dict(ONN,**{'ro.product.cpu.abilist':'armeabi-v7a,armeabi'}),TV_FEATURES,GTV)
        self.assertTrue(ok);self.assertEqual(found['abi'],'armeabi-v7a')
        self.assertEqual(dev.ASSETS[found['abi']][0],'Wholphin-armeabi-v7a.apk')

    def test_refusals(self):
        cases = [
            (dict(ONN, **{'ro.product.manufacturer': 'Google', 'ro.product.brand': 'google', 'ro.product.model': 'Chromecast'}), TV_FEATURES, GTV, 'not an onn.'),
            (ONN, 'feature:android.hardware.touchscreen\n', GTV, 'not a TV'),
            (ONN, TV_FEATURES, {'com.google.android.tvlauncher': '1'}, 'not Google TV'),
            (dict(ONN, **{'ro.kernel.qemu': '1'}), TV_FEATURES, GTV, 'emulator'),
            (dict(ONN, **{'ro.product.cpu.abilist': 'armeabi'}), TV_FEATURES, GTV, 'no pinned Wholphin'),
            (dict(ONN, **{'ro.product.brand': 'donnelly', 'ro.product.manufacturer': 'x', 'ro.product.model': 'y'}), TV_FEATURES, GTV, 'not an onn.'),
        ]
        for props, features, packages, reason in cases:
            ok, _, why = dev.identity(props, features, packages)
            self.assertFalse(ok)
            self.assertIn(reason, why)
        self.assertFalse(dev.identity(ONN, TV_FEATURES, GTV, expect_model='other')[0])

    def test_dry_run_accepts_only_the_emulator(self):
        emu = dict(ONN, **{'ro.product.cpu.abi': 'x86_64', 'ro.product.cpu.abilist':'x86_64,x86', 'ro.hardware': 'ranchu', 'ro.product.brand': 'google'})
        self.assertTrue(dev.identity(emu, TV_FEATURES, GTV, emulator=True)[0])
        self.assertFalse(dev.identity(ONN, TV_FEATURES, GTV, emulator=True)[0])

    def test_getprop_and_packages(self):
        props = dev.parse_getprop('[ro.product.model]: [onn. 4K Pro]\n[ro.empty]: []\n')
        self.assertEqual(props, {'ro.product.model': 'onn. 4K Pro', 'ro.empty': ''})
        self.assertEqual(dev.parse_packages('package:a.b versionCode:12\npackage:c\njunk\n'), {'a.b': '12', 'c': None})


class Connection(unittest.TestCase):
    def test_env_values_validated_and_never_prompted(self):
        env = dict(CLEANYFIN_TV_PAIR='192.168.4.30:37011', CLEANYFIN_TV_CODE='123456', CLEANYFIN_TV_CONNECT='192.168.4.30:41233')
        no = lambda *_: self.fail('prompted')
        self.assertEqual(dev.connection(env, no, no), ('192.168.4.30:37011', '123456', '192.168.4.30:41233'))
        self.assertEqual(dev.env_without_secrets(dict(env, PATH='/bin')), {'PATH': '/bin'})

    def test_bad_inputs_refused_without_echo(self):
        for pair, code, connect in (('8.8.8.8:5555', '123456', '8.8.8.8:5556'), ('192.168.4.30:1', '12345', '192.168.4.30:2'),
                                    ('192.168.4.30:1', '123456', '192.168.4.31:2'), ('box.local:1', '123456', '192.168.4.30:2'),
                                    ('192.168.4.30:1234', '123456', '192.168.4.30:5555'), ('127.0.0.1:5555', '123456', '127.0.0.1:5556')):
            with self.assertRaises(ValueError) as ctx:
                answers = iter([pair, connect])
                dev.connection({}, lambda _p: next(answers), lambda _p: code)
            self.assertNotIn(code, str(ctx.exception))
            self.assertNotIn(pair.split(':')[0], str(ctx.exception))


class ManifestAndRestore(unittest.TestCase):
    def test_diff_and_restore_put_back_every_touched_value(self):
        before = manifest()
        during = manifest(packages=dict(before['packages'], **{dev.APP: '63'}), wholphinVersion='1.1.2',
                          settings=dict(before['settings'], **{'global/encoded_surround_output': '1', 'secure/match_content_frame_rate': '2'}),
                          musicVolume={'value': 15, 'min': 0, 'max': 15}, labScratch=['lab-ui.xml'])
        changes = dev.diff(before, during)
        self.assertIn(f'+ package {dev.APP} 63', changes)
        self.assertIn('~ setting global/encoded_surround_output: None -> 1', changes)
        self.assertIn('~ media volume: 7 -> 15', changes)
        self.assertIn('+ file /data/local/tmp/lab-ui.xml', changes)
        plan = dev.restore_plan(before, during)
        self.assertIn(['shell', 'settings', 'delete', 'global', 'encoded_surround_output'], plan)
        self.assertIn(['shell', 'settings', 'put', 'secure', 'match_content_frame_rate', '1'], plan)
        self.assertIn(['shell', 'cmd', 'media_session', 'volume', '--stream', '3', '--set', '7'], plan)
        self.assertIn(['shell', 'rm', '-f', '/data/local/tmp/lab-ui.xml'], plan)
        # Never uninstalls, clears or edits anything not recorded as touched.
        self.assertFalse([c for c in plan if 'uninstall' in c or 'pm' in c or 'adb_wifi_enabled' in c])

    def test_restored_box_has_no_residue_except_a_kept_wholphin(self):
        before = manifest()
        kept = manifest(packages=dict(before['packages'], **{dev.APP: '63'}), wholphinVersion='1.1.2')
        self.assertEqual(dev.restore_plan(before, kept), [])
        self.assertEqual(dev.accepted_residue(before, kept, kept_wholphin=True), [])
        self.assertTrue(dev.accepted_residue(before, kept, kept_wholphin=False))
        self.assertEqual(dev.accepted_residue(before, manifest(), kept_wholphin=False), [])

    def test_family_copy_of_wholphin_is_never_counted_as_harness_residue(self):
        before = manifest(packages={dev.APP: '60'}, wholphinVersion='1.1.0')
        after = manifest(packages={dev.APP: '63'}, wholphinVersion='1.1.2')
        self.assertTrue(dev.accepted_residue(before, after, kept_wholphin=True))

    def test_unrelated_drift_is_reported_not_reverted(self):
        before = manifest()
        after = manifest(settings=dict(before['settings'], **{'global/adb_wifi_enabled': '0'}))
        self.assertEqual(dev.restore_plan(before, after), [])
        self.assertEqual(dev.diff(before, after), ['~ setting global/adb_wifi_enabled: 1 -> 0'])

    def test_manifest_reads_only(self):
        calls = []
        replies = {'list': 'package:a versionCode:1\n', 'get': 'null\n', 'volume': 'volume is 4 in range [0..15]\n',
                   'ls': 'lab-ui.xml\nother\n'}
        def adb(*args):
            calls.append(args)
            key = 'list' if 'list' in args else 'volume' if 'volume' in args else 'ls' if 'ls' in args else 'get'
            return replies[key]
        m = dev.manifest(adb)
        self.assertEqual(m['legacyAdb5555'],'unknown')
        self.assertEqual(dev.manifest(lambda *a:'LISTEN 0 4 *:5555 *:*' if 'ss' in a else adb(*a))['legacyAdb5555'],'listening')
        self.assertEqual((m['packages'], m['musicVolume']['value'], m['labScratch'], m['wholphinVersion']),
                         ({'a': '1'}, 4, ['lab-ui.xml'], None))
        for args in calls:
            self.assertNotIn('put', args); self.assertNotIn('--set', args); self.assertNotIn('rm', args)


class ArrivingAudio(unittest.TestCase):
    def test_bitstream_preamble_detected_only_frame_aligned(self):
        self.assertTrue(dev.iec61937(b'\0' * 8 + b'\x72\xf8\x1f\x4e' + b'\0' * 8))
        self.assertFalse(dev.iec61937(b'\0' * 6 + b'\x72\xf8\x1f\x4e' + b'\0' * 6))
        self.assertFalse(dev.iec61937(b'\x01\x02' * 100))

    def test_format_must_be_decoded_48k_stereo(self):
        self.assertTrue(dev.arriving_audio(dict(codec_name='pcm_s16le', sample_rate='48000', channels=2))[0])
        for probe in (dict(codec_name='pcm_s16le', sample_rate='44100', channels=2),
                      dict(codec_name='pcm_s16le', sample_rate='48000', channels=6),
                      dict(codec_name='ac3', sample_rate='48000', channels=2), {}):
            self.assertFalse(dev.arriving_audio(probe)[0])

    def test_media_session_state(self):
        dump = ('  Sessions Stack - have 2 sessions:\n    YouTube\n      package=com.google.android.youtube.tv\n'
                '      state=PlaybackState {state=3, position=0}\n    Wholphin\n'
                f'      package={dev.APP}\n      active=true\n      state=PlaybackState {{state=2, position=5000}}\n')
        self.assertEqual(dev.media_state(dump), 2)
        self.assertEqual(dev.media_state(dump.replace('{state=2,', '{state=PAUSED(2),')), 2)
        self.assertIsNone(dev.media_state('package=other\nstate=PlaybackState {state=3,'))

    def test_register_label_fits_schema_alphabet(self):
        text = ('onn onn. 4K Pro Streaming Device, Android 14 build onn/tvpro/tvpro:14/UTT3.240625.001/1:user/release-keys, '
                'HDMI capture MacroSilicon MS2130 (USB 3.0) YUYV 1920x1080@60, decoded stereo PCM')
        label = dev.register_label(text)
        self.assertRegex(label, dev.LABEL)
        self.assertLessEqual(len(dev.register_label(text * 3)), 256)
        self.assertIn('MS2130 USB 3.0 YUYV 1920x1080 60', label)

    def test_capture_file_mapping(self):
        with tempfile.NamedTemporaryFile(suffix='.mkv') as f:
            m = dev.capture_files([f.name, 'filtered=' + f.name])
            self.assertEqual(set(m), {'*', 'filtered'})
            self.assertEqual(dev.pick_capture(m, 'repeatControl'), Path(f.name).resolve())
        with self.assertRaises(ValueError):
            dev.capture_files(['control=/nonexistent/file.mkv'])


if __name__ == '__main__':
    unittest.main()
