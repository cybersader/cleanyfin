from unittest import TestCase
from tv_dolby_control import clock,verify


class ControlClock(TestCase):
    def test_mkv_derives_prefix_from_negative_pts(self):
        probe={'streams':[{'time_base':'1/1000','sample_rate':'48000'}], 'frames':[{'pts':-5+i*32,'nb_samples':1536} for i in range(625)]}
        self.assertEqual(clock(probe,0,960000),(-240,960000,240))
        prefix=b'\x55'*960;ref=b'\xaa'*(4*959760);verify(prefix+ref,prefix,ref,960000,240)
        for pre,body in [(prefix[:-1],ref),(b'\xbb'+prefix[1:],ref),(prefix,ref[:-4]),(prefix,b'\xbb'+ref[1:])]:
            with self.assertRaises(ValueError):verify(prefix+ref,pre,body,960000,240)

    def test_mp4_first_decoded_frame_already_skips_priming(self):
        probe={'streams':[{'time_base':'1/48000','sample_rate':'48000'}], 'frames':[{'pts':0,'nb_samples':1280}]+[{'pts':1280+i*1536,'nb_samples':1536} for i in range(624)]}
        self.assertEqual(clock(probe,0,960000),(0,959744,0));ref=b'\xaa'*(4*959744);verify(ref,b'',ref,959744,0)
        with self.assertRaises(ValueError):verify(ref+b'\0'*4,b'',ref,959744,0)

    def test_missing_fractional_or_discontinuous_clock_refused(self):
        for tb,frames in [('1/48000',[{'pts':0,'nb_samples':1536}]),('1/48000',[{'nb_samples':20}]),('1/96000',[{'pts':-1,'nb_samples':20}]),('1/48000',[{'pts':0,'nb_samples':20},{'pts':21,'nb_samples':20}])]:
            with self.assertRaises(ValueError):clock({'streams':[{'time_base':tb,'sample_rate':'48000'}],'frames':frames},0,960000)
