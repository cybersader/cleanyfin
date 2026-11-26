from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock
import numpy as np
from adb_lab import LabError
from adb_lab_playback import Playback
from tv_projection import active_track
from tv_channel_control import observation,calibration,predict

def compare(ref,raw,frames):
    try:return observation(ref,raw,2,2)
    except ValueError:return {'outcome':'unmeasured'}


class CaptureControl(TestCase):
    def setUp(self):
        self.x=np.random.default_rng(1729).integers(-8000,8000,(72000,2),dtype=np.int16)
        self.ref=self.x.astype('<i2').tobytes()

    def test_exact_requires_full_take_and_silent_padding(self):
        self.assertEqual(compare(self.ref,b'\0'*200+self.ref+b'\0'*400,len(self.x))['outcome'],'bit-exact')
        out=compare(self.ref,b'\1\0\0\0'+self.ref,len(self.x))
        self.assertEqual((out['outcome'],out['outsideNonzeroFrames']),('deviation',1))

    def test_gain_change_not_equal_and_truncation_not_deviation(self):
        raw=(self.x*2).astype('<i2').tobytes();out=compare(self.ref,raw,len(self.x))
        self.assertEqual(out['outcome'],'deviation');self.assertGreater(out['mismatchedValues'],100000)
        for raw in (b'',b'\0'*len(self.ref),raw[4000:],self.ref[:12000],self.ref+b'\0'):
            self.assertEqual(compare(self.ref,raw,len(self.x))['outcome'],'unmeasured')

    def test_only_matching_started_player(self):
        tv=Mock();line=' ID:42 type:android.media.AudioTrack u/pid: 111/222 state:started attr:AudioAttributes: usage=USAGE_MEDIA content=CONTENT_TYPE_MOVIE'
        for text in (line.replace('111/222','111/223'),line.replace('state:started','state:paused')):
            tv.transport.projection.return_value=text.encode();self.assertIsNone(active_track(tv,111,222))
        tv.transport.projection.return_value=line.encode();self.assertEqual(active_track(tv,111,222)['usage'],'USAGE_MEDIA')

    def test_no_projection_on_box_ordinary_or_update(self):
        for guest,family,mode,owned,installed in ((False,False,True,True,True),(True,True,True,True,True),
                (True,False,False,True,True),(True,False,True,False,True),(True,False,True,True,False)):
            p=Playback(Mock(),Path('/unused'),guest=guest,family=family);p.active=True;p.projection_helper=mode;p.owned_install=owned;p.helper_installed=installed;p._call=Mock()
            with self.assertRaises(LabError):p.projection('grant-audio')
            p._call.assert_not_called()
