"""Characterize the rendered TV PCM tap, without realigning either side of a cut."""
from pathlib import Path

import numpy as np

from exact_audio import RATE, compare, kept_pcm
from tv_score import TRIGGER

JOIN_HALF = RATE // 50


def pcm(raw):
    if len(raw) % 4:
        raise ValueError('Partial stereo frame')
    return np.frombuffer(raw, dtype='<i2').reshape((-1, 2))


def correlation(reference, capture, lo, hi):
    """Fit original frame zero in a bounded lag search; report competing peaks."""
    lo, hi = max(0, lo), min(hi, len(capture)-len(reference))
    if hi < lo or not np.any(reference):
        raise ValueError('No usable signal in bounded PCM alignment window')
    ref = reference.astype('float64')
    x = capture[lo:hi+len(ref)].astype('float64')
    dot = sum(np.correlate(x[:, ch], ref[:, ch]) for ch in (0, 1))
    xx = np.concatenate(([0.], np.cumsum(np.einsum('ij,ij->i', x, x))))
    power = xx[len(ref):] - xx[:-len(ref)]
    scale = np.sqrt(power * np.sum(ref**2))
    score = np.divide(dot, scale, out=np.full_like(dot, -1.0), where=scale > 0)
    idx = int(np.argmax(score))
    if score[idx] <= 0:
        raise ValueError('No positive PCM correlation')
    alt = np.concatenate((score[:max(0, idx-1)], score[idx+2:]))
    return lo+idx, dict(correlation=round(float(score[idx]), 12),
                        competingCorrelation=round(float(alt.max()), 12) if len(alt) else None)


def transfer(ref, actual):
    y, x = actual.astype('float64'), ref.astype('float64')
    power = (x*x).sum(axis=0)
    gain = np.divide((x*y).sum(axis=0), power, out=np.zeros(2), where=power > 0)
    return dict(gainLR=gain.tolist(), residualRmsLSB=np.sqrt(((y-x*gain)**2).mean(axis=0)).tolist())


def activity(a):
    indices = np.flatnonzero(np.any(a, axis=1))
    if not len(indices):
        raise ValueError('Silent PCM capture')
    return int(indices[0]), int(indices[-1])+1


def describe_render(raw, ref, output_source_ranges, join, suffix_at):
    a = pcm(raw)
    first, stop = activity(a)
    level=np.abs(a.astype('int32')).max(axis=1)
    signal=np.flatnonzero(level>=TRIGGER)
    source_signal=np.flatnonzero(np.abs(ref.astype('int32')).max(axis=1)>=TRIGGER)
    if not len(signal) or not len(source_signal):raise ValueError('No quiet-to-signal startup witness at the existing 512 LSB trigger')
    onset=int(signal[0])-int(source_signal[0])
    # The monitor may emit quiet dither far before the film; don't confuse it
    # with playback. Correlate an initial block within 512 samples either side
    # of the existing 512 LSB onset witness. Report nonzero and active spans.
    anchor = min(2048, join, len(ref))
    begin, fit = correlation(ref[:anchor], a, onset-512, onset+512)
    expected = sum(hi-lo for lo, hi in output_source_ranges)
    exact = compare(ref.tobytes(), a[first:stop].tobytes(), join)
    span=int(signal[-1]-signal[0])+1
    out = dict(captureFrames=len(a), firstNonzeroFrame=first, afterLastNonzeroFrame=stop,
               nonzeroSpanFrames=stop-first, expectedFrames=expected,
               nonzeroSpanDeltaFrames=stop-first-expected, frozenOriginSample=begin,
               signalSpanAt512LSBFrames=span, signalSpanDeltaFrames=span-expected,
               beforeSignalPeakLSB=int(level[:signal[0]].max(initial=0)),
               onsetAlignment=fit, againstDecodedAtNonzeroSpan=exact, probes={})
    for key, pos in {
        'early': RATE//10, 'beforeJoin': join-RATE//10,
        'cutStartTransition':join-1024, 'cutEndTransition':suffix_at-1024,
        'afterJoin': suffix_at+RATE//10, 'late': expected-RATE//4,
    }.items():
        block = ref[pos:pos+2048]
        if pos < 0 or len(block) != 2048:
            continue
        # Diagnostics reveal clock slips; these offsets never enter the splice
        # comparison. Tone-period competitors are reported, not resolved by fiat.
        try:delayed, witness = correlation(block, a, begin+pos-2048, begin+pos+2048)
        except ValueError:
            out['probes'][key]=dict(expectedCaptureFrame=begin+pos,limit='No usable correlation')
            continue
        src = pos
        walked = pos
        for lo, hi in output_source_ranges:
            if walked < hi-lo:
                src = lo+walked
                break
            walked -= hi-lo
        out['probes'][key] = dict(sourceFrame=src, expectedCaptureFrame=begin+pos,
                                 observedOffsetSamples=delayed-begin-pos, **witness,
                                 **transfer(block, a[delayed:delayed+len(block)]))
    if begin+expected > len(a):
        raise ValueError('Capture truncated at frozen origin')
    return out, a[begin:begin+expected]


def stats(a, b, origin):
    if a.shape != b.shape or not len(a):
        raise ValueError('PCM comparisons require equal nonempty stereo windows')
    diff = b.astype('int32')-a.astype('int32')
    changed = np.flatnonzero(np.any(diff, axis=1))
    energy = np.square(diff, dtype='int64').sum(axis=0)
    return dict(comparedSampleFrames=len(a), differentSampleFrames=len(changed),
                differentSamplesLR=np.count_nonzero(diff, axis=0).tolist(),
                firstDifferentFrame=int(changed[0])+origin if len(changed) else None,
                lastDifferentFrame=int(changed[-1])+origin if len(changed) else None,
                peakErrorLSB=np.abs(diff).max(axis=0).tolist(),
                errorEnergyLSB2=energy.tolist(), errorRmsLSB=np.sqrt(energy/len(a)).tolist(),
                referenceRmsLSB=np.sqrt(np.square(a, dtype='int64').mean(axis=0)).tolist())


def identity_counts(source, actual, start, end):
    """Only meaningful after the entire uncut lossless path has calibrated."""
    lookup = {frame: idx for idx, frame in enumerate(np.frombuffer(source, dtype='<u4'))}
    if len(lookup) != len(source)//4 or 0 in lookup:
        raise ValueError('Lossless sample identities are not unique and nonzero')
    marked = []
    kept = set()
    unknown = backwards = duplicates = 0
    previous = -1
    for out, word in enumerate(np.frombuffer(actual, dtype='<u4')):
        idx = lookup.get(word)
        if idx is None:
            unknown += 1
        elif start <= idx < end:
            marked.append((out, idx))
        else:
            backwards += idx < previous
            duplicates += idx in kept
            kept.add(idx)
            previous = idx
    return dict(matchedMarkedFrames=len(marked), firstMarkedMatches=marked[:16],
                lastMarkedMatch=marked[-1] if marked else None, unknownFrames=unknown,
                missingKeptFrames=len(lookup)-(end-start)-len(kept),
                keptRepeats=duplicates, keptBackwards=backwards)


def measure(source, paths, start, end, total, *, identifiable=False):
    """Measure two uncut takes before interpreting a filtered take of this codec."""
    full = [(0, total)]
    kept = [(0, start), (end, total)]
    length = total-end+start
    domains = {'prefix':(0,start), 'suffix':(start,length),
               'joinBefore20ms':(start-JOIN_HALF,start), 'joinAfter20ms':(start,start+JOIN_HALF)}
    result = dict(sampleRateHz=RATE, cutSourceFrames=[start, end], renders={})
    aligned = {}
    raw = {}
    for name, ranges in (('control',full),('repeatControl',full),('filtered',kept)):
        if paths.get(name) is None:
            continue
        raw[name] = Path(paths[name]).read_bytes()
        info, aligned[name] = describe_render(raw[name], pcm(kept_pcm(source,ranges)), ranges, start,
                                            start if name=='filtered' else end)
        result['renders'][name] = info
    if not {'control','repeatControl'} <= aligned.keys():
        return result
    def trim(control):
        return np.concatenate((control[:start], control[end:total]))
    a,b = (trim(aligned[n]) for n in ('control','repeatControl'))
    result['repeatability'] = {k:stats(a[lo:hi],b[lo:hi],lo) for k,(lo,hi) in domains.items()}
    result['uncutBitExact'] = all(result['renders'][k]['againstDecodedAtNonzeroSpan']['exact'] for k in ('control','repeatControl'))
    if 'filtered' not in aligned:
        return result
    c = aligned['filtered']
    result['controlVsFiltered'] = tested = {k:stats(a[lo:hi],c[lo:hi],lo) for k,(lo,hi) in domains.items()}
    for k, f in result['repeatability'].items():
        x = tested[k]
        x['withinFloor'] = x['differentSampleFrames'] <= f['differentSampleFrames'] and all(
            a <= b for key in ('peakErrorLSB','errorEnergyLSB2') for a,b in zip(x[key],f[key]))
        x['floorBelowSignal'] = all(e < s for e,s in zip(f['errorRmsLSB'],f['referenceRmsLSB']))
    if identifiable and result['uncutBitExact']:
        r = result['renders']['filtered']
        result['sampleIdentities'] = identity_counts(source[:total*4], raw['filtered'][r['firstNonzeroFrame']*4:r['afterLastNonzeroFrame']*4], start, end)
    return result
