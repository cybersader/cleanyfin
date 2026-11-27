"""Fast deterministic witness and interlock regression tests; no device/Docker."""
import base64
import unittest
import zlib
import numpy as np
from tv_r24 import material_claims
from tv_sound import Witness, source_pcm, WINDOW, HOP, RESOLUTION


class SoundTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src=(source_pcm(24000, 29)[:,:2]*32767).round().astype('<i2')
        cls.mark=(6503,14511); cls.w=Witness(cls.src.tobytes())
        a,b=cls.mark;cls.cut=np.concatenate([cls.src[:a],cls.src[b:]])

    def score(self,x):return self.w.score(x.tobytes(),self.mark)

    def test_ideal_cut_full_coverage_and_maps(self):
        for a,b in (self.mark,(0,9203),(19563,len(self.src))):
            y=np.concatenate([self.src[:a],self.src[b:]])
            obs=self.w.score(bytes(120)+y.tobytes()+bytes(240),(a,b))
            self.assertEqual((obs['outcome'],obs['zeroPaddingFrames']),('pass',[30,60]))
            for trace in obs['traceDeltaZlibLR']:
                starts=np.frombuffer(zlib.decompress(base64.b64decode(trace)),dtype='<i4').cumsum()
                self.assertEqual((starts[0],starts[-1]+WINDOW),(0,len(y)))
                self.assertLessEqual(np.diff(starts).max(),HOP)

    def test_replacement_in_either_ear_and_both(self):
        for ch in (None,0,1):
            for pos in (0,self.mark[0]-HOP//2,9101,len(self.cut)-RESOLUTION):
                y=self.cut.copy();idx=slice(None) if ch is None else ch
                y[pos:pos+RESOLUTION,idx]=self.src[8253:8253+RESOLUTION,idx]
                self.assertEqual(self.score(y)['outcome'],'fail')

    def test_unknown_is_not_a_negative_and_cannot_hide_a_positive(self):
        y=self.cut.copy();y[2800:3400]=0
        r=self.score(y);self.assertGreater(r['unidentifiedBlocks'],0);self.assertFalse(r['clear'])
        y[-480:]=self.src[8000:8480];self.assertEqual(self.score(y)['outcome'],'fail')
        for raw in (b'',b'x',bytes(8000),self.cut[:230].tobytes(),self.cut[HOP:].tobytes(),
                    np.concatenate([self.cut[:7700],self.cut[7900:]]).tobytes()):
            self.assertEqual(self.w.score(raw,self.mark)['outcome'],'unmeasured')

    def test_quiet_echo_cannot_be_cleared_by_a_high_position_correlation(self):
        y=self.cut.copy();y[3000:3480]+=self.src[8900:9380]//80
        r=self.score(y);self.assertEqual(r['blockOutcome'],'pass');self.assertFalse(r['clear'])
        self.assertGreater(r['maxKeptResidualLSB'],1)

    def test_independent_route_challenge_requires_both_actual_controls(self):
        from tv_sound import route_challenge
        captures={}
        for label,gain in [('positive',.57),('held-out',.83)]:
            raw=np.rint(self.src*gain).astype('<i2').tobytes();r=self.w.score(raw)
            self.assertTrue(r['clear']);captures[label]=(raw,{'soundControl':r,'soundCalibrated':True})
        self.assertTrue(route_challenge(self.w,captures,self.mark)['passed'])
        captures['held-out'][1]['soundCalibrated']=False
        self.assertFalse(route_challenge(self.w,captures,self.mark)['passed'])

    def test_uniform_clock_drift_and_quantized_channel_gains(self):
        for ppm in (-400,400):
            t=np.arange(0,len(self.cut)-.5,1+ppm/1e6)
            y=np.stack([np.interp(t,np.arange(len(self.cut)),self.cut[:,i])*g
                        for i,g in enumerate([.61,.47])],axis=1).round().astype('<i2')
            row=self.score(y);self.assertTrue(row['clear'],row['reason'])
            self.assertLessEqual(row['maxKeptResidualLSB'],1)

    def test_short_mark_and_unknown_transport_are_not_measured(self):
        y=np.concatenate([self.src[:3000],self.src[3048:]])
        self.assertFalse(self.w.score(y.tobytes(),(3000,3048))['clear'])
        self.assertFalse(self.w.score(self.cut.tobytes(),self.mark,None)['clear'])

    def test_positive_survives_partial_sweep_and_safety_is_not_material(self):
        row={'positions':[{'plannedTakes':['left','right'],'takes':{'left':{'sound':{'markedSoundDetections':3}}}}]}
        self.assertEqual(material_claims(row,'stopped')['marked sound'][0],'FAIL')
        for name,v in material_claims({},None).items():self.assertEqual(v[0],'not-measured',name)
        partial={'plannedTakes':['left','right'],'takes':{'left':{'sound':{'clear':True}}}}
        self.assertEqual(material_claims({'positions':[partial]*24},None)['marked sound'][0],'not-measured')

    def test_host_endpoint_attribution(self):
        from test_tv_safety import h
        before=('a',['a'],'s',['s']);sink='cf_lab_test'
        churn=h.host_audio_cleanup(before,('a',['a','other'],'s',['s','other.monitor']),sink)
        self.assertTrue(churn['ownedHostAudioAbsent']);self.assertFalse(churn['hostSinkListUnchanged'])
        for after in (('a',['a',sink],'s',['s']),('a',['a'],'s',['s',sink+'.monitor'])):
            self.assertFalse(h.host_audio_cleanup(before,after,sink)['ownedHostAudioAbsent'])
