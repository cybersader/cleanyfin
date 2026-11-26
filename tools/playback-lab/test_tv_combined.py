"""Synthetic only: detector preconditions, independent seam measurements and capture authority."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace
import numpy as np
from adb_lab import LabError
from adb_lab_playback import Playback
from tv_marked import marked_audio,seam_observation

class CombinedTests(unittest.TestCase):
    def test_actual_detector_refuses_without_packets(self):
        for raw,reason in ((b'','empty audio'),(b'\1'*8192,'no AO packet trace')):
            row=marked_audio(raw);self.assertFalse(row['clear']);self.assertEqual(row['reason'],reason)
            self.assertNotIn('markedSoundDetections',row)

    def test_two_uncut_controls_before_local_seam_level_evidence(self):
        x=np.random.default_rng(73).integers(-10000,10000,(24000,2),dtype='int16');raw=x.tobytes();mark=SimpleNamespace(a=6500,b=14500)
        cut=np.vstack([x[:mark.a],np.zeros((130,2),'int16'),x[mark.b:]]).tobytes();controls=[bytes(4096)+raw,raw+bytes(4096)]
        obs=seam_observation(raw,controls,cut,mark,0,'straight');self.assertEqual(obs['cutLandmarkGapFrames'],130)
        self.assertEqual(obs['cutWindow']['longestQuietFrames'],130);self.assertIn('unmeasured',obs['sync'])
        self.assertNotIn('cutWindow',seam_observation(raw,[raw,b''],cut,mark,0,'straight'))
        self.assertNotIn('cutWindow',seam_observation(raw,controls,cut,mark,0,'seek-resume'))

    def test_helper_is_fresh_no_replace_and_never_bypasses_stock_consent(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            p=Playback(Mock(),Path(tmp),guest=False,family=True);p.qualify('armeabi-v7a');apk=Path(tmp)/'input/input.apk';apk.parent.mkdir();apk.write_bytes(b'synthetic');sha=hashlib.sha256(apk.read_bytes()).hexdigest()
            p._call=Mock(return_value=b'package:android\npackage:org.cleanyfin.fixture');
            with self.assertRaises(LabError):p.install_capture_helper(apk,sha)
            self.assertEqual(p._call.call_count,1)
            p._call=Mock(side_effect=[b'package:android',b'Success']);p.install_capture_helper(apk,sha)
            self.assertEqual(p._call.call_args.args[0],('install','--no-streaming','/payload.apk'))
            p._call=Mock()
            for op in ('allow','default','policy'):
                with self.assertRaises(LabError):p.projection(op)
            p._call.assert_not_called();p.projection('start');self.assertEqual(p._call.call_args.args[0][-2:],('-a','stereo'))
            with self.assertRaises(LabError):p.run('uninstall','com.github.damontecres.wholphin')
