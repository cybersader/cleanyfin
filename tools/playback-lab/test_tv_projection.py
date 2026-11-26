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

    def test_native_six_float_requires_all_six_lanes(self):
        x=np.random.default_rng(40).random((72000,6),dtype=np.float32)-.5
        self.assertEqual(observation(x.tobytes(),bytes(2400)+x.tobytes(),6,4)['outcome'],'bit-exact')
        y=x.copy();y[:,5]=0;out=observation(x.tobytes(),y.tobytes(),6,4)
        self.assertEqual(out['outcome'],'deviation');self.assertGreater(out['mismatchedValues'],71000)

    def test_matrix_frozen_before_held_out_channel_pulses(self):
        x=np.zeros((14*48000,6),np.float32);rng=np.random.default_rng(713)
        for k in range(2):
            for c in range(6):
                a=(1+c+k*6)*48000;x[a:a+38400,c]=rng.uniform(-.15,.15,38400)
        m=np.array([[1,0],[0,.8],[.5,.5],[0,0],[.3,0],[0,.2]]);raw=x.astype('<f4').tobytes()
        y=predict(raw,m,2);recovered,result=calibration(raw,bytes(4096)+y,2,2)
        self.assertTrue(result['validated']);self.assertEqual(result['quantization'],'nearest-even');np.testing.assert_allclose(recovered,m,atol=.0001)
        cut=7*48000*4;bad=y[:cut]+predict(raw,m*.72,2)[cut:]
        self.assertFalse(calibration(raw,bytes(4096)+bad,2,2)[1]['validated'])
