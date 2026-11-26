"""R24 desktop marked-material detector; independent of R22 seam scoring. No admission from a null alone.

Train a marked-vector neighbourhood on an uncut cold start with changed decoder history. Challenge it
with an independent paused in-player seek/resume. A vector outside that calibrated neighbourhood is a
negative *detector* result, not proof of kept identity. Exact, uniquely located unchanged packet samples
already have stronger source identity. Anything else must face the positive detector, all channels at once.
"""
from fractions import Fraction
import numpy as np
from scipy.spatial import cKDTree
import format_measure as fm
import r22_oracle as r22


def capture(runner, source, ref, label, options, start=None):
    args = [*options] if start is None else [*options, f'--start={float(start):.6f}']
    frames, wav, extra = runner.capture(label, source, ref['channels'], *args)
    pcm = fm.read_pcm(wav, ref['channels'])
    return runner.sequence(frames), pcm, r22.read_clocks(extra['clockTrace'], pcm, ref['channels'])


def positive_slice(take, ref, start, end):
    """Locate the alternate decoder's *actual* full marked interval, not merely nominal seek PTS.

    The suffix length nominates a source start. Require anchored exact multichannel windows on both sides
    of the measured region (or at its interior for marks at a file boundary), and a complete uncut tail.
    A short/shifted control is not usable to infer sensitivity.
    """
    seq, pcm, (packets, _) = take
    if not seq or seq[0] is None or seq != list(range(seq[0], fm.FRAMES)) or not packets or len(take[2][1]) < len(seq):
        raise fm.Unmeasured('incomplete positive control')
    x, y = (r22.pcm_array(v, ref['channels']) for v in (ref['pcm'], pcm))
    require_packets(packets, len(y))
    offset = len(x) - len(y)
    if not 0 <= offset <= start < end <= len(x):
        raise fm.Unmeasured('positive control did not present the entire marked interval')
    for at in (max(offset, min(start, len(x)-96) - 96), max(offset, min(end, len(x)-96))):
        a = x[at:at+96].tobytes()
        if len(a) != 96 * 2 * ref['channels'] or ref['pcm'].count(a) != 1 or y[at-offset:at-offset+96].tobytes() != a:
            raise fm.Unmeasured('positive control source position unverified')
    return y[start-offset:end-offset]


def require_packets(packets, length):
    end = 0
    for _, a, b in packets:
        if end != a or not a < b <= length:
            raise fm.Unmeasured('incomplete AO packet trace')
        end = b
    if not length or end != length:
        raise fm.Unmeasured('incomplete AO coverage')


class Detector:
    def __init__(self, reference, trained, held_out):
        if reference.shape != trained.shape or held_out.shape != reference.shape or not len(reference):
            raise fm.Unmeasured('missing positive controls')
        self.tree = cKDTree(reference)
        # Freeze the tolerance before looking at the independent seek/resume or the filtered takes.
        self.radius = float(np.max(np.linalg.norm(trained.astype(float) - reference, axis=1)))
        self.size = len(reference)
        # A held-out control identical to the original verifies exact positives, not sensitivity
        # to a new lossy value. Never clear a residual outside the observed deviation envelope.
        self.held_radius = float(np.max(np.linalg.norm(held_out.astype(float) - reference, axis=1)))
        self.residual_limit = min(self.radius, self.held_radius)
        if self.count(held_out) != self.size:
            raise fm.Unmeasured('held-out single-sample marked positives not all detected')

    def count(self, samples):
        if not len(samples): return 0
        distances, _ = self.tree.query(samples, distance_upper_bound=max(1e-6, self.radius+1e-6))
        return int(np.count_nonzero(distances <= self.radius))


def calibrate(runner, ref, mark, options):
    source = ref['source']; ch = ref['channels']; a = ref['lead'] + mark.a
    x = r22.pcm_array(ref['pcm'], ch); b = min(ref['lead'] + mark.b, len(x))
    # Two different decoder histories near the mark. Begin early enough to capture its leading edge.
    frame = max(0, mark.a // fm.SAMPLES_PER_FRAME - 1); t = frame / 25
    trained = positive_slice(capture(runner, source, ref, 'r24-positive-start', options, t), ref, a, b)
    targets = [(None, 0), ((fm.FRAMES-5)/25, fm.FRAMES-5), (t, frame)]
    resumed = fm.seeks(runner, source, options, targets, marked_frames=set(), resume_channels=ch)
    if not resumed['ok']: raise fm.Unmeasured('positive seek not verified')
    held_out = positive_slice(resumed['resume'], ref, a, b)
    return Detector(x[a:b], trained, held_out)


def seam_report(take, ref, mark, signature, first, target):
    """Report R22 observations on this very output, not on an earlier plugin build.

    Do not rerun R22 admission or invent a cold-start transient allowance. The onset margin is
    unmeasured without its own control; a splice comparison, when available, remains in the receipt.
    """
    seq, pcm, clocks = take
    at = (ref['lead'] if mark.a else 0) + mark.kept_index(first*fm.SAMPLES_PER_FRAME) if target is not None else 0
    join = (ref['lead'] if mark.a else 0) + mark.a - at
    ideal = mark.expected(ref)[at*signature.width:]
    if not ideal: return {'unmeasured': 'no aligned kept interval'}
    measured = r22.observe(pcm, *clocks, seq, source=signature, mark=mark, control=ref['r22Clock'], ideal=ideal,
        ideal_at=at, join=join, picture_ok=list(seq)==[f for f in mark.kept_frames if f>=first], exact_issues=[],
        fresh=target is not None, onset_omit_first=target==mark.join_seconds)
    keys = ('extraZeroSamples','extraQuietSamples','transientComparisons','transientMarginRms','pictureIntact',
            'extraVideoTimingMs','worstAvOffsetMs','recoveryMs','staysInSync','keptSoundLossMs','keptSoundLossLowerBoundMs')
    return {key: measured.get(key) for key in keys}


def score(take, ref, mark, detector, signature, first):
    seq, pcm, (packets, video) = take
    if not seq or any(f is None for f in seq) or len(video) < len(seq) or not packets or not ref['r22Clock']:
        raise fm.Unmeasured('missing barcode or packet coverage')
    if not pcm: raise fm.Unmeasured('empty audio')
    y = r22.pcm_array(pcm, ref['channels']); span = mark.b-mark.a
    require_packets(packets, len(y))
    # A one-frame or truncated capture is not a measured playback interval. Incorrect interior
    # kept pictures remain a seam report, not a reason to suppress a positive marked detection.
    marked_frames = sum(f in mark.marked_frames for f in seq)
    if not marked_frames and (first != seq[0] or seq[-1] != mark.kept_frames[-1]):
        raise fm.Unmeasured('requested landing or playback end was not witnessed')
    rebase = float(np.median(np.array(video[-len(seq):]) - np.array([float(mark.frame_time(f))*1000 for f in seq])))
    exact_marked = possible = residual = collisions = 0
    for pts, start, stop in packets:
        nominal = round(pts - (rebase+ref['r22Clock']['audioBiasMs'])*fm.RATE/1000)
        part = y[start:stop]; at = signature.locate(part.tobytes(), (ref['lead']+nominal, ref['lead']+nominal+span))
        if at is None or at < 0 or at+len(part) > len(signature.x):
            raise fm.Unmeasured('no unique source anchor for packet')
        x = signature.x[at:at+len(part)]
        exact = np.all(x == part, axis=1)
        lo, hi = max(0, signature.a-at), min(len(part), signature.b-at)
        # Known marked location is never exonerated by changed decoder history.
        if hi > lo: exact_marked += hi-lo
        uncertain = ~exact
        if hi > lo: uncertain[lo:hi] = False
        # Even a byte-identical kept vector is ambiguous if the positive detector also calls it
        # marked. A packet anchor on its neighbours must not exonerate a one-sample substitution.
        exact_kept = exact.copy()
        if hi > lo: exact_kept[lo:hi] = False
        collisions += detector.count(part[exact_kept])
        if uncertain.any():
            d = np.linalg.norm(part[uncertain].astype(float) - x[uncertain], axis=1)
            n = detector.count(part[uncertain]); possible += n; residual += len(d)
            if not n and np.max(d) > detector.residual_limit:
                raise fm.Unmeasured('residual outside held-out decoder-history calibration')
    return dict(markedFrames=marked_frames, markedSoundDetections=exact_marked+possible+collisions, exactKeptCollisions=collisions,
                anchoredMarkedSamples=exact_marked, residualMatches=possible, residualSamples=residual,
                presentedSamples=len(y), presentedPictures=len(seq))


def sweep(runner, source, ref, positions, options, arm, save):
    results = []
    for a, b in positions:
        av = ref['cut']['videoAnchorMs']; duration = ref['cut']['durationMs']-av
        b = min(b, duration); mark = fm.Mark(ref['cut'], av+a, av+b)
        row = dict(mark=mark.label(), takes={}, passed=False); results.append(row)
        try:
            arm(a, b, b == duration)
            detector = calibrate(runner, ref, mark, options)
            row.update(positiveControlsPassed=True, positiveSamples=detector.size, radiusLSB=detector.radius, heldOutDeviationLSB=detector.held_radius)
            signature = r22.SourceFingerprint(ref['pcm'], ref['channels'], ref['lead'], mark.a, mark.b)
            j = mark.join_seconds
            targets = sorted({max(Fraction(0), j-Fraction(1,1000)), j, j+Fraction(52,100), max(Fraction(0), j-Fraction(2,10))})
            for t in [None, *(t for t in targets if mark.landing(t) is not None)]:
                key = 'straight' if t is None else f'start-{float(t):.3f}'
                take = capture(runner, source, ref, 'r24-take', options, t)
                first = mark.kept_frames[0] if t is None else mark.landing(t)
                row['takes'][key] = score(take, ref, mark, detector, signature, first)
                row['takes'][key]['seams'] = seam_report(take, ref, mark, signature, first, t)
            before = j-Fraction(413,10000); resume = j if mark.landing(j) is not None else before
            seeks = [(None, mark.kept_frames[0])] + [(float(t), mark.landing(t)) for t in (
                j,j-Fraction(13,10000),before,j+Fraction(10007,10000),Fraction(1,2),resume) if t >= 0 and mark.landing(t) is not None]
            landed = fm.seeks(runner, source(), options, seeks, marked_frames=mark.marked_frames, resume_channels=ref['channels'])
            row['seekLandings'] = [{k:l[k] for k in ('target','presented','markedCount','unreadable')} for l in landed['landings']]
            row['takes']['resume'] = score(landed['resume'], ref, mark, detector, signature, mark.landing(resume))
            row['takes']['resume']['seams'] = seam_report(landed['resume'], ref, mark, signature, mark.landing(resume), resume)
            row['passed'] = bool(landed['ok'] and len(row['seekLandings']) == len(seeks) and
                                 all(not t['markedFrames'] and not t['markedSoundDetections'] for t in row['takes'].values()))
        except fm.Unmeasured as exc: row['unmeasured'] = str(exc)
        save(results)
        print(f"    R24 {mark.label()}: {row.get('unmeasured') or ('clear' if row['passed'] else 'refused')}", flush=True)
    return results
