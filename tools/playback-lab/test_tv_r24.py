import unittest
import numpy as np
from tv_r24 import aligned_control, SoundGate, resolution, verdict


class ExternalDolbyTap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Unique stereo values; marked sound away from the ends.
        cls.x=np.arange(10000,20000,dtype='<u4').tobytes();cls.take=bytes(400)+cls.x+bytes(800)

    def test_uncut_capture_must_have_unique_clock_and_match_completely(self):
        self.assertEqual(aligned_control(self.x,self.take),self.x)
        for bad in (self.take[:-1000],self.take+self.take,self.take+b'\1\0\0\0',bytes(len(self.take))):
            with self.subTest(),self.assertRaises(ValueError):aligned_control(self.x,bad)

    def test_one_marked_frame_of_stereo_pcm_is_never_a_negative(self):
        gate=SoundGate(self.x,self.take,self.take,100,200)
        safe=self.x[:400]+self.x[800:];self.assertTrue(gate.score(safe)['clear'])
        self.assertEqual(gate.score(safe+self.x[400:404])['markedSoundDetections'],1)
        self.assertFalse(gate.score(safe+b'\xff'*4)['clear'])
        with self.assertRaises(ValueError):gate.score(bytes(32))

    def test_positive_before_an_interruption_is_a_failure_not_an_unmeasured_negative(self):
        partial={'positions':[{'clear':False,'takes':{'cut':{'markedPictures':1}}}]}
        self.assertEqual(verdict(partial,'audio capture failed')[0],'FAIL')
        self.assertEqual(verdict({},'no calibrated capture')[0],'not-measured')
        self.assertEqual(verdict({'admitted':True,'positions':[]},None)[0],'not-measured')

    def test_collision_is_not_assumed_kept(self):
        x=self.x[:404]+self.x[:4]+self.x[408:];g=SoundGate(x,x,x,100,200)
        self.assertEqual(g.score(x[:4])['ambiguous'],1);self.assertFalse(g.score(x[:4])['clear'])

    def test_sampling_must_resolve_one_display_refresh_not_just_one_movie_frame(self):
        info={'presentationTiming':{'layers':[{'periodNs':16666667,'presentCount':100}]}}
        self.assertTrue(resolution({'fullCaptureMaxGapMs':16.1},info))
        self.assertFalse(resolution({'fullCaptureMaxGapMs':17.1},info))
        self.assertFalse(resolution({'fullCaptureMaxGapMs':8,'interiorUnreadable':1},info))
        self.assertFalse(resolution({'fullCaptureMaxGapMs':8},{}))
