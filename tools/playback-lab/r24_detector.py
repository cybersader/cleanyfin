"""R24 desktop marked-material sweep, detector v2: source-position identity of every presented sample frame.

Replaces the v1 decoded-vector neighbourhood (radius around marked vectors calibrated on extra positive captures).
That detector fired on exact known-kept PCM because the tone fixture puts marked and kept vectors on one curve, and
its calibration captures were themselves a source of unmeasured positions. v2 needs the source-time code fixture
(format_fixtures 'timecode') and asks marked_oracle.judge() where each output frame came from:

* every take must be complete (AO packet trace covering all bytes, sound and picture to end of file) and fully
  identified (exact, or positionally resolved; an undecidable altered packet group is unmeasured);
* marked = exact-marked + positionally marked + within tau of a decoded marked vector;
* positives are injected into that very take (boundary, start, end and interior; exact, decoder-altered and near
  values; 1..8 frames) and every one must be detected; the ideal cut must give zero alarms;
* per layout, held-out uncut captures (fresh starts and a paused seek-resume) and two held-out decoders (Jellyfin
  10.11's ffmpeg 7.1.4 and 12.4's ffmpeg 8.1.3) are judged against every sweep mark sample by sample, against an
  independent cross-correlation alignment: sensitivity and specificity counts.

A take that did not finish, could not be identified or had a missed positive never clears its position. Seam
measures (R22 silence/pop/picture/sync/loss) are reported, never gating.
"""
from fractions import Fraction
import os
import subprocess

import numpy as np

import format_fixtures as ff
import format_measure as fm
import marked_oracle as mo
import r22_oracle as r22

VERSION = 'r24-marked-v2'
HELD_OUT_STARTS = (2.0, 7.37, 13.013)
HELD_OUT_SEEK = 9.5


def capture(runner, source, ref, label, options, start=None, retries=1):
    """One take; a player exit is retried once with a fresh offer, and the retry is recorded."""
    args = [*options] if start is None else [*options, f'--start={float(start):.6f}']
    for attempt in range(retries + 1):
        try:
            frames, wav, extra = runner.capture(label, source, ref['channels'], *args)
            break
        except fm.Unmeasured as exc:
            if 'exited' not in str(exc) or attempt == retries:
                raise
    pcm = fm.read_pcm(wav, ref['channels'])
    return runner.sequence(frames), pcm, r22.read_clocks(extra['clockTrace'], pcm, ref['channels']), attempt


def code_lead(u, lead):
    """Independent check of the source clock: the code channels' cross-correlation lag must equal the tone's."""
    src = ff.timecode_pcm(u.shape[1]).astype(np.float64)
    n, best = min(200_000, len(u) - 16), []
    for lag in range(-8, 9):
        if 2000 + lag < 0:
            continue
        score = 0.0
        for c in (1, 2, 4, 5)[:max(1, u.shape[1] - 2)]:
            a = u[2000 + lag:n + lag, c].astype(np.float64)
            b = src[2000:n, c]
            score += float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) or 1))
        best.append((score, lag))
    best.sort(reverse=True)
    if best[0][1] != lead or best[0][0] < 2 * best[1][0]:
        raise fm.Unmeasured(f'source-time code lag {best[0][1]} disagrees with the tone lead {lead}')
    return dict(lag=best[0][1], score=round(best[0][0], 4), runnerUp=round(best[1][0], 4))


def align_start(y, code, near=None):
    """Independent (cross-correlation) start of a contiguous uncut playback, the truth for the controls. Uses the
    code channels only and a probe 4096 frames in, so decoder priming before the source cannot steer it; the result
    may be negative (priming presented before uncut index 0)."""
    cols = [c for c in range(code.u.shape[1]) if c not in (0, 3)] or [0]
    skip = 4096 if len(y) > 4096 + 8192 else 0
    probe = y[skip:skip + 8192][:, cols].astype(np.float64)
    w = len(probe)
    centre = 0 if near is None else near + skip
    lo = 0 if near is None else max(0, centre - 96_000)
    hi = len(code.u) - w if near is None else min(len(code.u) - w, centre + 96_000)
    seg = code.u[lo:hi + w][:, cols].astype(np.float64)
    total = None
    for i in range(len(cols)):
        a, b = seg[:, i], probe[:, i]
        size = 1 << (len(a) + len(b)).bit_length()
        corr = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)[:len(a) - len(b) + 1]
        total = corr if total is None else total + corr
    return lo + int(np.argmax(total)) - skip


def score(take, ref, code, mark, lo, hi, tau, first, seed):
    seq, pcm, (packets, _), retries = take
    if not pcm:
        raise fm.Unmeasured('empty audio')
    if not packets:
        raise fm.Unmeasured('no AO packet trace')
    y = r22.pcm_array(pcm, ref['channels'])
    marked_frames = sum(f in mark.marked_frames for f in seq if f is not None)
    unreadable = sum(f is None for f in seq)
    if not seq or seq[-1] != mark.kept_frames[-1]:
        raise fm.Unmeasured('picture did not reach the end of file')
    v = mo.judge(y, packets, code, lo, hi, tau)
    # The take must present sound through the file's last sample (or the frame before a mark that runs to the end);
    # an early stop is a coverage gap, never a pass.
    end = len(code.u) - 1 if hi < len(code.u) - fm.RATE // 1000 else lo - 1
    if v.lastPosition != end and v.detections == 0:
        raise fm.Unmeasured(f'sound did not reach the end of file ({v.lastPosition} != {end})')
    challenges = mo.run_challenges(y, packets, code, lo, hi, tau, seed=seed)
    return dict(markedFrames=marked_frames, unreadablePictures=unreadable, presentedPictures=len(seq),
                landing=dict(expected=first, presented=seq[0]), playerRetries=retries,
                sound=v.as_dict(), markedSoundDetections=v.detections,
                challenges=challenges,
                challengesPlanned=sum(c['planned'] for c in challenges.values()),
                challengesMissed=sum(c['missed'] for c in challenges.values()),
                challengesRefused=sum(c['refused'] for c in challenges.values()))


def seam_report(take, ref, mark, first, target):
    """R22 observations on this output (reported, never gating). Failures are recorded, not raised."""
    seq, pcm, clocks, _ = take
    try:
        signature = r22.SourceFingerprint(ref['pcm'], ref['channels'], ref['lead'], mark.a, mark.b)
        at = (ref['lead'] if mark.a else 0) + mark.kept_index(first*fm.SAMPLES_PER_FRAME) if target is not None else 0
        join = (ref['lead'] if mark.a else 0) + mark.a - at
        ideal = mark.expected(ref)[at*2*ref['channels']:]
        if not ideal:
            return {'unmeasured': 'no aligned kept interval'}
        measured = r22.observe(pcm, *clocks, seq, source=signature, mark=mark, control=ref['r22Clock'], ideal=ideal,
                               ideal_at=at, join=join, picture_ok=list(seq) == [f for f in mark.kept_frames if f >= first],
                               exact_issues=[], fresh=target is not None, onset_omit_first=target == mark.join_seconds)
    except Exception as exc:  # reported only
        return {'unmeasured': f'{type(exc).__name__}: {exc}'[:200]}
    keys = ('extraZeroSamples', 'extraQuietSamples', 'transientMarginRms', 'pictureIntact', 'extraVideoTimingMs',
            'worstAvOffsetMs', 'recoveryMs', 'staysInSync', 'keptSoundLossMs', 'keptSoundLossLowerBoundMs')
    return {key: measured.get(key) for key in keys}


def take_schedule(mark):
    j = mark.join_seconds
    starts = sorted({max(Fraction(0), j - Fraction(1, 1000)), j, j + Fraction(52, 100), max(Fraction(0), j - Fraction(2, 10))})
    return [None, *(t for t in starts if mark.landing(t) is not None)]


def take_key(t):
    return 'straight' if t is None else f'start-{float(t):.3f}'


def position(runner, source, ref, code, rel, options, arm, seeds, seams=True):
    a, b = rel
    av = ref['cut']['videoAnchorMs']
    duration = ref['cut']['durationMs'] - av
    b = min(b, duration)
    mark = fm.Mark(ref['cut'], av + a, av + b)
    lo, hi = code.lead + mark.a, code.lead + mark.b
    row = dict(mark=mark.label(), samples=[mark.a, mark.b], takes={}, planned=[], passed=False)
    try:
        arm(a, b, b == duration)
    except fm.Unmeasured as exc:
        row['refusedOffer' if 'no filtered offer' in str(exc) else 'unmeasured'] = str(exc)[:300]
        return row
    try:
        tau, closest = mo.tau_for(code, lo, hi)
        row.update(tauLSB=tau, minKeptToMarkedLSB=closest)
        ideal_y, ideal_p = mo.ideal_cut(code, lo, hi)
        iv = mo.judge(ideal_y, ideal_p, code, lo, hi, tau)
        row['idealCut'] = dict(detections=iv.detections, nonExact=iv.presented - iv.exactKept, presented=iv.presented)
        if iv.detections or iv.presented != iv.exactKept:
            raise fm.Unmeasured('detector alarms on the ideal cut')
        schedule = take_schedule(mark)
        j = mark.join_seconds
        before = j - Fraction(413, 10000)
        resume = j if mark.landing(j) is not None else before
        row['planned'] = [take_key(t) for t in schedule] + ['resume']
        for t in schedule:
            key = take_key(t)
            first = mark.kept_frames[0] if t is None else mark.landing(t)
            take = capture(runner, source, ref, 'r24-take', options, t)
            row['takes'][key] = score(take, ref, code, mark, lo, hi, tau, first, next(seeds))
            if seams:
                row['takes'][key]['seams'] = seam_report(take, ref, mark, first, t)
        seeks = [(None, mark.kept_frames[0])] + [(float(t), mark.landing(t)) for t in (
            j, j - Fraction(13, 10000), before, j + Fraction(10007, 10000), Fraction(1, 2), resume)
            if t >= 0 and mark.landing(t) is not None]
        landed = fm.seeks(runner, source() if callable(source) else source, options, seeks, marked_frames=mark.marked_frames, resume_channels=ref['channels'])
        row['seekLandings'] = [{k: l[k] for k in ('target', 'expected', 'presented', 'markedCount', 'unreadable')}
                               for l in landed['landings']]
        take = (*landed['resume'], 0)
        row['takes']['resume'] = score(take, ref, code, mark, lo, hi, tau, mark.landing(resume), next(seeds))
        if seams:
            row['takes']['resume']['seams'] = seam_report(take, ref, mark, mark.landing(resume), resume)
        takes = row['takes'].values()
        row['passed'] = bool(list(row['takes']) == row['planned'] and len(row['seekLandings']) == len(seeks) and
                             all(not l['markedCount'] and not l['unreadable'] for l in row['seekLandings']) and
                             all(not t['markedFrames'] and not t['markedSoundDetections'] and not t['challengesMissed']
                                 for t in takes))
    except fm.Unmeasured as exc:
        row['unmeasured'] = str(exc)[:300]
    except mo.Unmeasured as exc:
        row['unmeasured'] = f'oracle: {exc}'[:300]
    return row


def _expected_counts(n, start, lo, hi):
    """Marked frames presented by a contiguous playback of N frames from uncut index START."""
    return max(0, min(start + n, hi) - max(start, lo))


def control_counts(y, packets, code, marks, near=None, clip=False):
    """Judge a held-out uncut playback against every mark; per-sample truth from cross-correlation alignment.
    CLIP: judge only the span the reference decode covers (another decoder may also emit the file's final frame,
    which mpv never presents and the reference therefore cannot identify)."""
    start = align_start(y, code, near)
    if clip and start + len(y) > len(code.u):
        y = y[:len(code.u) - start]
        packets = [(t, a, min(b, len(y))) for t, a, b in packets if a < len(y)]
    tally = dict(tp=0, fn=0, fp=0, tn=0, unmeasured=0, marks=0)
    for lo, hi in marks:
        tally['marks'] += 1
        truth = set(range(max(start, lo), min(start + len(y), hi)))
        try:
            v = mo.judge(y, packets, code, lo, hi)
        except mo.Unmeasured:
            tally['unmeasured'] += 1
            continue
        found = set(v.markedPositions)
        tally['tp'] += len(found & truth)
        tally['fn'] += len(truth - found)
        tally['fp'] += len(found - truth) + max(0, v.detections - len(found))
        tally['tn'] += len(y) - len(truth) - len(found - truth)
    return dict(startIndex=int(start), presented=len(y), **tally)


def held_out_controls(runner, ref, code, marks, options, fixtures=None, file=None):
    """Uncut fresh starts, an uncut paused seek-resume, and two other decoders, judged against every mark."""
    out = {}
    for t in HELD_OUT_STARTS:
        key = f'mpv-start-{t}'
        try:
            seq, pcm, (packets, _), _ = capture(runner, ref['source'], ref, 'r24-control', options, t)
            y = r22.pcm_array(pcm, ref['channels'])
            out[key] = control_counts(y, packets, code, marks, near=code.lead + int(t*fm.RATE))
        except (fm.Unmeasured, mo.Unmeasured) as exc:
            out[key] = dict(unmeasured=str(exc)[:200])
    frame = round(HELD_OUT_SEEK*fm.FPS)
    try:
        landed = fm.seeks(runner, ref['source'], options, [(None, 0), (HELD_OUT_SEEK, frame)], marked_frames=set(),
                          resume_channels=ref['channels'])
        seq, pcm, (packets, _) = landed['resume']
        out['mpv-seek-resume'] = control_counts(r22.pcm_array(pcm, ref['channels']), packets, code, marks,
                                                near=code.lead + int(HELD_OUT_SEEK*fm.RATE))
    except (fm.Unmeasured, mo.Unmeasured) as exc:
        out['mpv-seek-resume'] = dict(unmeasured=str(exc)[:200])
    if fixtures is not None:
        from hard_fixture_lab import JELLYFIN
        for line, j in JELLYFIN.items():
            for ss in (None, '7.37'):
                key = f"ffmpeg-{line}-{'straight' if ss is None else 'ss' + ss}"
                cmd = ['docker', 'run', '--rm', '--network', 'none', '--memory', '2g', '--cpus', '2',
                       '--user', f'{os.getuid()}:{os.getgid()}', '-v', f'{fixtures}:/lab:ro,Z',
                       '--entrypoint', '/usr/lib/jellyfin-ffmpeg/ffmpeg', j['image'], '-hide_banner', '-loglevel', 'error',
                       '-nostdin', '-cons_noisegen', '1', *(['-ss', ss] if ss else []), '-i', f'/lab/{file}',
                       '-map', '0:a:0', '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1']
                try:
                    raw = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=300).stdout
                    y = np.frombuffer(raw, '<i2').reshape(-1, ref['channels'])
                    packets = [(0, a, min(a + 1536, len(y))) for a in range(0, len(y), 1536)]
                    near = code.lead + (int(float(ss)*fm.RATE) if ss else 0)
                    out[key] = control_counts(y, packets, code, marks, near=near, clip=True) | dict(decoder=line)
                except (subprocess.SubprocessError, ValueError, mo.Unmeasured) as exc:
                    out[key] = dict(unmeasured=f'{type(exc).__name__}: {exc}'[:200])
    return out


def sweep(runner, source, ref, positions, options, arm, save, fixtures=None, file=None, seams=True):
    """Every sweep position, plus layout-level detector controls. SAVE(results) checkpoints after each position."""
    u = r22.pcm_array(ref['pcm'], ref['channels'])
    code = mo.SourceCode(u, ref['lead'])
    av = ref['cut']['videoAnchorMs']
    duration = ref['cut']['durationMs'] - av
    marks = []
    for a, b in positions:
        m = fm.Mark(ref['cut'], av + a, av + min(b, duration))
        marks.append((code.lead + m.a, code.lead + m.b))
    layout = dict(version=VERSION, uniqueFraction=code.unique_fraction, uncutFrames=len(u),
                  codeLead=code_lead(u, ref['lead']), positions=[])
    layout['controls'] = held_out_controls(runner, ref, code, marks, options, fixtures, file)
    save(layout)
    seeds = iter(range(1, 10**6))
    for rel in positions:
        row = position(runner, source, ref, code, rel, options, arm, seeds, seams)
        layout['positions'].append(row)
        save(layout)
        print(f"    R24v2 {row['mark']}: " + (row.get('refusedOffer') and 'refused offer' or row.get('unmeasured') or
                                             ('clear' if row['passed'] else 'NOT clear')), flush=True)
    return layout


def summarize(layout, planned_positions):
    """Plain per-layout counts: never 'none got through' unless every planned take was scored."""
    pos = layout.get('positions', [])
    takes_planned = sum(len(p.get('planned') or []) for p in pos)
    takes_scored = sum(len(p.get('takes', {})) for p in pos)
    s = dict(positionsPlanned=planned_positions, positionsAttempted=len(pos),
             positionsClear=sum(bool(p.get('passed')) for p in pos),
             positionsUnmeasured=sum('unmeasured' in p for p in pos),
             positionsRefusedOffer=sum('refusedOffer' in p for p in pos),
             positionsNotClear=sum(not p.get('passed') and 'unmeasured' not in p and 'refusedOffer' not in p for p in pos),
             takesPlanned=takes_planned, takesScored=takes_scored,
             markedFrames=sum(t['markedFrames'] for p in pos for t in p.get('takes', {}).values()),
             markedSeekPictures=sum(l['markedCount'] for p in pos for l in p.get('seekLandings', [])),
             markedSoundDetections=sum(t['markedSoundDetections'] for p in pos for t in p.get('takes', {}).values()),
             challengesPlanned=sum(t['challengesPlanned'] for p in pos for t in p.get('takes', {}).values()),
             challengesMissed=sum(t['challengesMissed'] for p in pos for t in p.get('takes', {}).values()),
             challengesRefused=sum(t['challengesRefused'] for p in pos for t in p.get('takes', {}).values()),
             alteredKeptFrames=sum(t['sound']['alteredKept'] for p in pos for t in p.get('takes', {}).values()),
             playerRetries=sum(t.get('playerRetries', 0) for p in pos for t in p.get('takes', {}).values()))
    ctl = [c for c in layout.get('controls', {}).values() if 'tp' in c]
    for k in ('tp', 'fn', 'fp', 'tn', 'unmeasured'):
        s['control' + k.upper() if k != 'unmeasured' else 'controlUnmeasuredMarks'] = sum(c[k] for c in ctl)
    s['controlsUnmeasured'] = sum('tp' not in c for c in layout.get('controls', {}).values())
    complete = (s['positionsAttempted'] == planned_positions and s['positionsClear'] == planned_positions)
    s['admitted'] = bool(complete and not s['markedFrames'] and not s['markedSeekPictures'] and
                         not s['markedSoundDetections'] and not s['challengesMissed'] and not s['controlFN'] and
                         not s['controlFP'] and not s['controlsUnmeasured'] and not s['controlUnmeasuredMarks'])
    s['statement'] = ('zero marked material at every position (every planned take scored)' if s['admitted'] else
                      'no confirmed leak in retained observations; not admitted' if not s['markedFrames'] and
                      not s['markedSoundDetections'] and not s['markedSeekPictures'] else 'marked material detected')
    return s
