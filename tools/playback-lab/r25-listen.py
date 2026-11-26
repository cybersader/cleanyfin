#!/usr/bin/env python3
"""Make a synthetic music/dialogue A/B at the strongest measured R25 masking ratio.

Encode both an original and an original with opposite polarity strictly inside
its mark. With aligned kept frames, [(restarted − continuous)+ − (restarted −
continuous)−]/2 isolates the restart difference that follows marked input. This
controlled contrast can include nonlinear codec response; it is a demonstration,
not the estimator or evidence on which admission depends.
"""
import argparse
import csv
from functools import partial
import importlib.util
import io
import json
from math import ceil, gcd
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.io import wavfile
from scipy.signal import correlate, resample_poly

import format_fixtures as ff
import format_measure as fm
import marked_oracle as mo
import r22_oracle as r22
import r24_detector as r24
from r25_echo import BLOCK, db

SPEC = importlib.util.spec_from_file_location('echo_sweep', Path(__file__).with_name('r25-sweep.py'))
sweep = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sweep)
RATE = fm.RATE


def speech(phrase):
    audio = subprocess.check_output(['espeak-ng', '--stdout', '-s', '152', '-p', '43', phrase])
    rate, pcm = wavfile.read(io.BytesIO(audio))
    g = gcd(rate, RATE)
    return .21 * resample_poly(pcm.astype(float) / 32768, RATE // g, rate // g)


def program(lo, hi):
    n = ff.SOURCE_SAMPLES
    rng = np.random.default_rng(25)
    # Plucked strings and a warm sustained pad, spread over the soundstage.
    x = rng.normal(0, .0005, (n, 6))
    for beat, chord in enumerate(((110., 164.81, 261.63), (98., 146.83, 246.94),
                                  (130.81, 196., 293.66), (82.41, 123.47, 220.)) * 9):
        at = round(beat * .57 * RATE)
        t = np.arange(min(round(.9 * RATE), n - at)) / RATE
        for j, f in enumerate(chord):
            pluck = (1 - np.exp(-t * 370)) * np.exp(-t * (3.6 + j))
            pluck *= .036 * (np.sin(2 * np.pi * f * t) + .25 * np.sin(4 * np.pi * f * t + .6)
                             + .08 * np.sin(6 * np.pi * f * t + 1.4))
            pan = (.95, .35, .12, .08, .44, .31) if j % 2 else (.35, .95, .11, .08, .31, .44)
            x[at:at + len(t)] += pluck[:, None] * pan
    stem = np.array([.28, .25, 1., 0., .07, .05])

    def add(at, v):
        a, b = max(0, at), min(n, at + len(v))
        if b > a:
            x[a:b] += v[a - at:b - at, None] * stem

    before = speech('We should take the river path.')
    add(lo - len(before) + int(.10 * RATE), before)
    after = speech('The bridge is just ahead.')
    add(hi + int(.065 * RATE), after)
    # End on a voiced syllable rather than eSpeak's default tail silence.
    # Keep the marked utterance entirely in the mark, not the kept dialogue.
    turn = speech('Right around the corner.')
    peak = int(np.argmax(np.convolve(turn ** 2, np.ones(256), mode='same')))
    turn = turn[:peak + 120][-int(hi - lo):]
    add(hi - len(turn), turn)
    return x.astype(np.float32)


def reference(raw, runner, *, control_source, probe, channels, mpv_options):
    _, wav, _ = runner.capture('program-control', control_source, channels, *mpv_options)
    pcm = fm.read_pcm(wav, channels)
    u = r22.pcm_array(pcm, channels)
    # Independent source-clock alignment; use several channels and a segment
    # away from priming. Compare all lags at once, not using the join residual.
    a, b = RATE, 3 * RATE
    corr = sum(correlate(u[a - 8:b + 8, c].astype(float), raw[a:b, c], mode='valid')
               for c in (0, 1, 2, 4, 5))
    lead = int(np.argmax(corr)) - 8
    if abs(lead) == 8:
        raise ValueError('program source clock is not resolved inside the search window')
    return dict(cut=fm.plan_for(probe), channels=channels, pcm=pcm, lead=lead)


def capture(pair, target_key, runner, source, ref, positions, options, arm, save, **_):
    a, b = positions[0]
    cut = ref['cut']
    av = cut['videoAnchorMs']
    mark = fm.Mark(cut, av + a, av + b)
    target = {r24.take_key(t): t for t in r24.take_schedule(mark)} | {'resume': None}
    arm(a, b, False)
    u = r22.pcm_array(ref['pcm'], 6)
    code = mo.SourceCode(u, ref['lead'])
    lo, hi = mark.a + code.lead, mark.b + code.lead
    y, packets = sweep.recapture(runner, source, ref, options, mark, target[target_key], target_key)
    p, exact, _ = mo.assign(y, packets, code, lo, hi)
    idx = np.where(exact >= 0, exact, p)
    if np.any((idx >= lo) & (idx < hi)):
        raise ValueError('program presentation entered the mark')
    if not np.any((idx >= hi) & (idx < hi + BLOCK) & (exact < 0)):
        raise ValueError('program did not reproduce a history-less restart at this join')
    pair.update(uncut=u, actual=y, idx=idx, lo=lo, hi=hi, lead=code.lead)
    return {'positions': [dict(mark=mark.label())]}


def stereo(x):
    x = np.asarray(x, dtype=float)
    return x[:, :2] + .70710678 * (x[:, 2:3] + x[:, 4:6])


def loudness(x):
    r = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-f', 'f32le', '-ar', str(RATE), '-ac', '2',
                        '-i', 'pipe:0', '-af', 'ebur128', '-f', 'null', '-'], check=True,
                       input=x.astype('<f4').tobytes(), stderr=subprocess.PIPE)
    return float(re.findall(r'I:\s+(-?[\d.]+) LUFS', r.stderr.decode())[-1])


def wav(path, x):
    if np.max(np.abs(x)) >= 32767.5 / 32768:
        raise ValueError('output clips')
    wavfile.write(path, RATE, np.rint(x * 32768).astype('<i2'))


def envelope(out, echo_frames, kept_frames, layout, ml, mh):
    # One dBFS scale, fixed 0.25 ms bins; weak/absent echo remains as a gap.
    bins = []
    for a in range(0, len(echo_frames), 12):
        b = min(a + 12, len(echo_frames))
        bins.append((1000 * (a + b) / (2 * RATE),
                     db(float(np.sqrt(np.mean(kept_frames[a:b] ** 2)))),
                     db(float(np.sqrt(np.mean(echo_frames[a:b] ** 2))))))
    with (out / 'echo-envelope.csv').open('w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['ms_after_mark', 'kept_rms_dbfs', 'restart_marked_response_rms_dbfs'])
        w.writerows(bins)
    bg, ink, muted, grid = '#fcfcfb', '#0b0b0b', '#52514e', '#deddd8'
    kept_color, echo_color = '#2a78d6', '#eb6834'
    im = Image.new('RGB', (1200, 660), bg)
    draw = ImageDraw.Draw(im)
    regular = subprocess.check_output(['fc-match', '-f', '%{file}', 'sans'], text=True)
    bold = subprocess.check_output(['fc-match', '-f', '%{file}', 'sans:style=Bold'], text=True)

    def label(x, y, text, size, color=ink, *, heading=False, anchor=None):
        draw.text((x, y), text, color, ImageFont.truetype(bold if heading else regular, size), anchor=anchor)

    label(68, 36, 'What remains after the cut?', 42, heading=True)
    label(70, 99, f'Synthetic speech + music  /  {layout}  /  mark [{ml}, {mh}) ms', 23, muted)
    draw.line((70, 163, 106, 163), kept_color, 3)
    label(118, 148, 'Kept sound', 22)
    for k in (305, 320, 335):
        draw.line((k, 163, k + 9, 163), echo_color, 3)
    label(361, 148, 'Marked-dependent restart response (unamplified)', 22)
    l, r, top, bottom = 142, 1130, 239, 520
    span = max(6., len(echo_frames) * 1000 / RATE)
    yy = lambda v: bottom - (bottom - top) * (v + 100) / 100
    xx = lambda v: l + (r - l) * v / span
    label(l, 201, 'RMS per 0.25 ms window (dBFS)', 21, muted)
    for value in range(0, -101, -20):
        y = yy(value)
        draw.line((l, y, r, y), grid, 1)
        label(l - 14, y, str(value), 20, muted, anchor='rm')
    for tick in range(0, ceil(span), 2):
        x = xx(tick)
        draw.line((x, bottom, x, bottom + 5), grid, 1)
        label(x, bottom + 14, str(tick), 20, muted, anchor='mt')
    label((l + r) / 2, bottom + 48, 'Time after the mark ends (ms)', 21, muted, anchor='mt')
    for i, col in ((1, kept_color), (2, echo_color)):
        for a, b in zip(bins, bins[1:]):
            if a[i] is None or b[i] is None:
                continue
            x1, y1, x2, y2 = xx(a[0]), yy(max(-100, a[i])), xx(b[0]), yy(max(-100, b[i]))
            if i == 2:
                x2, y2 = x1 + .59 * (x2 - x1), y1 + .59 * (y2 - y1)
            draw.line((x1, y1, x2, y2), col, 3)
    label(70, 622, 'Opposite marked polarity control. This illustration is not the timecode admission measure.', 20, muted)
    im.save(out / 'echo-envelope.png')


def export(out, pair, inverted, name, a, b, key, line):
    u, y, ids, lo, hi = (pair[k] for k in ('uncut', 'actual', 'idx', 'lo', 'hi'))
    v, z, js, hi2 = (inverted[k] for k in ('uncut', 'actual', 'idx', 'hi'))
    # It is only valid to subtract frames that truly refer to the same kept time.
    common, ia, ib = np.intersect1d(ids - hi, js - hi2, return_indices=True)
    first = int(common[(common >= 0) & (common < BLOCK)][0])
    last = 2 * BLOCK
    ids_in = (common >= first) & (common < last)
    p = common[ids_in]
    if not np.array_equal(p, np.arange(first, last)):
        raise ValueError('program controls do not cover the complete comparison window')
    delta = .5 * ((y[ia[ids_in]].astype(float) - u[hi + p]) - (z[ib[ids_in]].astype(float) - v[hi2 + p]))
    # The window beyond the transform is an independently observed null control.
    if np.max(np.abs(delta[p >= BLOCK]), initial=0.) > 2:
        raise ValueError('unexpected late difference in the polarity control')
    ms = (abs(ids - hi) if lo == 0 else np.where(ids < lo, lo - ids, ids - hi))
    take = (ms <= 2 * RATE) & (ids >= 0)
    actual = stereo(y[take]) / 32768
    start = max(0, lo - 2 * RATE)
    ideal = stereo(np.concatenate((u[start:lo], u[hi:hi + 2 * RATE + 1]))) / 32768
    # For a take beginning near or after the join, use the same available left
    # context without putting unplayed audio back into the actual comparison.
    if (ids[0] >= lo and ids[0] < hi) or (lo > 0 and ids[0] >= hi):
        ideal = stereo(u[hi:hi + 2 * RATE + 1]) / 32768
    elif lo > 0 and 0 <= ids[0] < lo and ids[0] > start:
        ideal = ideal[ids[0] - start:]
    li, la = loudness(ideal), loudness(actual)
    target = min(-23., li, la)
    ideal, actual = ideal * 10 ** ((target - li) / 20), actual * 10 ** ((target - la) / 20)
    wav(out / 'ideal.wav', ideal)
    wav(out / 'actual.wav', actual)
    isolated = np.zeros((last, 2))
    isolated[first:last] = stereo(delta) / 32768
    kept = stereo(u[hi:hi + last]) / 32768
    # A/B are loudness-matched; the isolated burst deliberately is not. Repeat
    # the burst with space between occurrences so its timbre is easy to hear.
    peak = float(np.max(np.abs(isolated)))
    if not peak:
        raise ValueError('no marked-dependent program response was isolated')
    gain = min(30., 20 * np.log10(.25 / peak))
    period = np.zeros((RATE // 2, 2))
    period[6000:6000 + last] = isolated * 10 ** (gain / 20)
    wav(out / 'echo-only-amplified.wav', np.tile(period, (5, 1)))
    envelope(out, isolated, kept, name, a, b)
    (out / 'clip-facts.json').write_text(json.dumps(dict(layout=name, jellyfin=line, markMs=[a, b], take=key,
        isolatedPeakDbfs=db(peak), echoOnlyGainDb=gain, matchedLoudnessLUFS={'ideal': loudness(ideal), 'actual': loudness(actual)},
        comparison='Synthetic 5.1 to stereo. Echo only is half the difference between restart deltas when only the marked interval polarity is inverted, not a literal replay of marked frames.'), indent=2) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('receipt', type=Path)
    p.add_argument('out', type=Path)
    args = p.parse_args()
    if not os.environ.get('CLEANYFIN_NUGET_FEED'):
        sys.exit('Set CLEANYFIN_NUGET_FEED for offline builds.')
    receipt = json.loads(args.receipt.read_text())
    if not receipt.get('complete'):
        raise ValueError('listening clip selection requires the complete sweep')
    candidates = [(v['rmsRelativeDb'], v['peakDbfs'], line, name, p['samples'], key, row['tuple']['pluginBuild'])
                  for line, runs in receipt['runs'].items() for name, row in runs.items() if name.startswith('mkv-')
                  for p in row['positions'] for key, take in p['takes'].items() for block in take.get('postBlocks', [])
                  for v in block['channels'] if v['rmsRelativeDb'] is not None]
    # Prefer straight play on a numerical tie, then the older line for reproducibility.
    best = max(candidates, key=lambda c: (c[0], c[1], c[5] == 'straight', c[2] == '10.11'))
    _, _, line, name, (lo, hi), key, plugin = best
    a, b = lo // 48, hi // 48
    pcm = program(lo, hi)
    pair = [{}, {}]
    matrix = sweep.matrix
    scratch = matrix.ROOT / 'plugin/obj'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='r25-listen-', dir=scratch) as tmp:
        work = Path(tmp)
        for i in range(2):
            raw = pcm.copy()
            if i:
                raw[lo:hi] *= -1
            fixtures = work / f'movies-{i}'
            fixtures.mkdir()
            ff.generate(fixtures, name, program_pcm=raw)
            lab = matrix.FormatLab(work / f'jf-{i}', line, fixtures, [name], matrix.mpv_identity())
            lab.work.mkdir()
            lab.case_timeout = 120
            lab.r24_progress = lambda *_: None
            lab.r24_sweep = partial(capture, pair[i], key)
            try:
                lab.setup()
                lab.prepare()
                sweep.require(lab.plugin_build == plugin, 'listening lab plugin build differs')
                lab.measure(name, [(a, b)], reference=partial(reference, raw))
                matrix.cp.docker('stop', lab.cid)
                lab.inspect()
            finally:
                if lab.cid:
                    matrix.cp.docker('rm', '-f', '-v', lab.cid)
                shutil.rmtree(lab.work)
    args.out.mkdir(parents=True, exist_ok=False)
    export(args.out, *pair, name, a, b, key, line)


if __name__ == '__main__':
    main()
