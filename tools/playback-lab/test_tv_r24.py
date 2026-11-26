import unittest
from tv_r24 import resolution, verdict


class ExternalDolbyTap(unittest.TestCase):
    def test_positive_before_an_interruption_is_a_failure_not_an_unmeasured_negative(self):
        partial={'positions':[{'clear':False,'takes':{'cut':{'markedPictures':1}}}]}
        self.assertEqual(verdict(partial,'audio capture failed')[0],'FAIL')
        self.assertEqual(verdict({},'no calibrated capture')[0],'not-measured')
        self.assertEqual(verdict({'admitted':True,'positions':[]},None)[0],'not-measured')

    def test_sampling_must_resolve_one_display_refresh_not_just_one_movie_frame(self):
        info={'presentationTiming':{'layers':[{'periodNs':16666667,'presentCount':100}]}}
        self.assertTrue(resolution({'fullCaptureMaxGapMs':16.1},info))
        self.assertFalse(resolution({'fullCaptureMaxGapMs':17.1},info))
        self.assertFalse(resolution({'fullCaptureMaxGapMs':8,'interiorUnreadable':1},info))
        self.assertFalse(resolution({'fullCaptureMaxGapMs':8},{}))
