"""Pure tests of the TV audio clock and SurfaceFlinger receipts."""
from pathlib import Path
import random
import struct
import tempfile
import unittest

from tv_pcm import measure
from tv_score import parse_latency


class TvPcmTests(unittest.TestCase):
    def test_identifiable_marked_substitution_and_length(self):
        start, end, total=10000,13000,40000
        src=random.Random(185).randbytes(total*4)
        with tempfile.TemporaryDirectory() as tmp:
            def write(name, data, pad):
                file=Path(tmp)/name
                file.write_bytes(bytes(pad*4)+data+bytes(200*4));return file
            actual=src[:(start-1)*4]+src[(end-1)*4:(total-1)*4]
            result=measure(src,{'control':write('a',src,700),'repeatControl':write('b',src,900),
                                'filtered':write('c',actual,1200)},start,end,total,identifiable=True)
        self.assertTrue(result['uncutBitExact'])
        self.assertEqual(result['sampleIdentities']['firstMarkedMatches'],[(start-1,end-1)])
        self.assertEqual(result['sampleIdentities']['missingKeptFrames'],2)
        self.assertEqual(result['renders']['filtered']['againstDecodedAtNonzeroSpan']['delta'],-1)
        self.assertEqual(result['controlVsFiltered']['joinBefore20ms']['firstDifferentFrame'],start-1)
        self.assertEqual(result['repeatability']['joinAfter20ms']['differentSampleFrames'],0)

    def test_dither_is_not_used_as_audio_start_and_slip_is_not_realigned(self):
        start,end,total=10000,13000,40000
        src=random.Random(117).randbytes(total*4)
        with tempfile.TemporaryDirectory() as tmp:
            def write(name,data,pad):
                file=Path(tmp)/name
                file.write_bytes(struct.pack('<hh',2,-2)+bytes(pad*4)+data+bytes(500*4));return file
            a=write('a',src,700)
            b=write('b',src,1500)
            filtered=src[:start*4]+bytes(77*4)+src[end*4:]
            c=write('c',filtered,1000)
            result=measure(src,{'control':a,'repeatControl':b,'filtered':c},start,end,total)
        self.assertFalse(result['uncutBitExact'])
        self.assertEqual(result['renders']['filtered']['frozenOriginSample'],1001)
        self.assertEqual(result['renders']['filtered']['probes']['afterJoin']['observedOffsetSamples'],77)
        self.assertEqual(result['controlVsFiltered']['joinBefore20ms']['differentSampleFrames'],0)
        self.assertEqual(result['controlVsFiltered']['joinAfter20ms']['differentSampleFrames'],960)

    def test_surface_latency_uses_only_completed_actual_presents(self):
        period,presents=parse_latency(b'16666666\n0 0 0\n18 21 25\n19 9223372036854775807 25\n27 31 38\n')
        self.assertEqual(period,16666666)
        self.assertEqual(presents,{21,31})
        with self.assertRaises(ValueError):parse_latency(b'not a trace\n')


if __name__=='__main__':unittest.main()
