#!/usr/bin/env python3
"""Replay changed Dolby takes from the archived R24 v2 receipts on disposable Jellyfin.

Bit-exact kept takes have zero delta relative to the uninterrupted decode, by
construction; the archive supplies that witness. Recapture every take with a
changed kept frame through the same ticketed mpv path and match the archived PCM
identity/deviation summary before estimating the reflected marked component.
Stores only numerical summaries. Does not edit or generate register rows.
"""
import argparse
from fractions import Fraction
from functools import partial
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np

import format_fixtures as ff
import format_measure as fm
import marked_oracle as mo
import r22_oracle as r22
import r24_detector as r24
import r25_echo as echo

SPEC = importlib.util.spec_from_file_location('matrix', Path(__file__).with_name('format-matrix.py'))
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)
ARCHIVE = Path(__file__).resolve().parent / 'prototypes/common-formats'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def recapture(runner, source, ref, options, mark, target, key):
    if key != 'resume':
        _, pcm, (packets, _), _ = r24.capture(runner, source, ref, 'r25-take', options, target)
    else:
        j = mark.join_seconds
        before = j - Fraction(413, 10000)
        resume = j if mark.landing(j) is not None else before
        seeks = [(None, mark.kept_frames[0])] + [(float(t), mark.landing(t)) for t in
                 (j, j - Fraction(13, 10000), before, j + Fraction(10007, 10000), Fraction(1, 2), resume)
                 if t >= 0 and mark.landing(t) is not None]
        _, pcm, (packets, _) = fm.seeks(runner, source(), options, seeks,
                                      marked_frames=mark.marked_frames, resume_channels=6)['resume']
    return r22.pcm_array(pcm, 6), packets


def analyze(y, packets, code, raw, mark, take):
    lo, hi = mark.a + code.lead, mark.b + code.lead
    pos, exact, _ = mo.assign(y, packets, code, lo, hi)
    ident = np.where(exact >= 0, exact, pos)
    require(not np.any((ident >= lo) & (ident < hi)), 'replay presented a marked position')
    changed = (exact < 0) & (ident >= 0) & (ident < len(code.u))
    ids = ident[changed]
    diff = y[changed].astype(float) - code.u[ids]
    require(len(y) == take['presented'] and len(ids) == take['alteredKept'] and
            ident[0] == take['firstPosition'] and ident[-1] == take['lastPosition'] and
            abs(np.max(np.linalg.norm(diff, axis=1), initial=0.) - take['maxAlteredLSB']) < 1e-7,
            'replay did not reproduce the archived PCM summary')
    post = ids >= hi
    p, d, kept = ids[post] - code.lead, diff[post], code.u[ids[post]]
    post_blocks = []
    for block in np.unique(p // echo.BLOCK):
        i = p // echo.BLOCK == block
        estimate = echo.project(raw, p[i], d[i], kept[i], mark.a, mark.b)
        post_blocks.append(dict(first=int(p[i][0]), last=int(p[i][-1]), alteredFrames=int(i.sum()),
                                reflectedMarkedFrames=len(estimate.positions) if estimate else 0,
                                channels=estimate.rows() if estimate else []))
    return dict(method='replayed', alteredFrames=len(ids), preMarkAlteredFrames=int((ids < lo).sum()),
                firstAfterMark=int(ident[ident >= hi][0] - code.lead) if (ident >= hi).any() else None,
                maxAlteredVectorLSB=take['maxAlteredLSB'], postBlocks=post_blocks)


def replay(archive, runner, source, ref, positions, options, arm, save, **_):
    u = r22.pcm_array(ref['pcm'], ref['channels'])
    code = mo.SourceCode(u, ref['lead'])
    raw = ff.timecode_pcm()
    lead = r24.code_lead(u, ref['lead'])
    require(len(u) == archive['uncutFrames'] and lead == archive['codeLead'], 'uncut replay mismatch')
    layout = dict(uncutFrames=len(u), codeLead=lead, positions=[])
    cut = ref['cut']
    av = cut['videoAnchorMs']
    duration = cut['durationMs'] - av
    for (a, b), old in zip(positions, archive['positions'], strict=True):
        b = min(b, duration)
        mark = fm.Mark(cut, av + a, av + b)
        targets = {r24.take_key(t): t for t in r24.take_schedule(mark)} | {'resume': None}
        require(old['passed'] and list(targets) == old['planned'] == list(old['takes']) and
                [mark.a, mark.b] == old['samples'], 'archive mark/take mismatch')
        row = dict(mark=old['mark'], samples=old['samples'], takes={})
        todo = {key: t['sound'] for key, t in old['takes'].items() if t['sound']['alteredKept']}
        if todo:
            arm(a, b, b == duration)
        for key, old_take in old['takes'].items():
            take = old_take['sound']
            require(not old_take['markedSoundDetections'], 'R24 must independently clear the take')
            if key not in todo:
                require(take['exactKept'] <= take['presented'] <= take['exactKept'] + take['leadIn'] and
                        not take['maxAlteredLSB'], 'archive does not prove byte-exact kept samples')
                row['takes'][key] = dict(method='archive-exact', alteredFrames=0)
                continue
            y, packets = recapture(runner, source, ref, options, mark, targets[key], key)
            row['takes'][key] = analyze(y, packets, code, raw, mark, take)
        layout['positions'].append(row)
        save(layout)
        if todo:
            print(f'    {mark.label()}: replay matched {len(todo)} altered takes', flush=True)
    return layout


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('receipt', type=Path)
    p.add_argument('--jellyfin', default='both', choices=('both', *matrix.JELLYFIN))
    p.add_argument('--layout', action='append', choices=matrix.DOLBY_LAYOUTS)
    args = p.parse_args()
    if not os.environ.get('CLEANYFIN_NUGET_FEED'):
        sys.exit('Set CLEANYFIN_NUGET_FEED to the staged offline package directory.')
    lines = sorted(matrix.JELLYFIN) if args.jellyfin == 'both' else [args.jellyfin]
    names = args.layout or list(matrix.DOLBY_LAYOUTS)
    mpv = matrix.mpv_identity()
    out = dict(detector=r24.VERSION, echoEstimator=echo.VERSION, absoluteDbfs=echo.ABSOLUTE_DBFS,
               maskingDb=echo.MASKING_DB, archiveSha256={}, mpv=dict(image=mpv[0], version=mpv[1]), runs={})

    def save():
        pending = args.receipt.with_name(args.receipt.name + '.pending')
        pending.write_text(json.dumps(out, indent=2, allow_nan=False) + '\n')
        pending.replace(args.receipt)

    scratch = matrix.ROOT / 'plugin/obj'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='r25-', dir=scratch) as tmp:
        work = Path(tmp)
        fixtures = work / 'movies'
        fixtures.mkdir()
        facts = {n: ff.generate(fixtures, n, 'timecode') for n in names}
        for line in lines:
            path = ARCHIVE / f'receipt-r24v2-{line}-2026-11-26.json.gz'
            out['archiveSha256'][line] = hashlib.sha256(path.read_bytes()).hexdigest()
            with gzip.open(path) as f:
                archive = json.load(f)
            require(archive['detector'] == r24.VERSION and archive['mpv'] == out['mpv'] and
                    archive['playerOptions'] == matrix.PLAYER_OPTIONS and archive['requestedPositionCount'] == 24,
                    'archived detector, player or schedule changed')
            run = out['runs'][line] = {}
            lab = matrix.FormatLab(work / line, line, fixtures, names, mpv)
            lab.case_timeout = 120
            lab.work.mkdir()
            try:
                lab.setup()
                lab.prepare()
                require(lab.plugin_build == archive['pluginBuild'][line], 'live build differs from archived plugin')
                for name in names:
                    old = archive['runs'][line][name]
                    require(old['summary']['admitted'], 'archive did not pass R24')
                    for key in ('size', 'video', 'audio', 'subtitles'):
                        require(facts[name][key] == archive['fixtures'][name][key], f'fixture {key} differs: {name}')
                    print(f'[{line}] {name}', flush=True)
                    lab.r24_sweep = partial(replay, old['result']['r24Sweep'])

                    def progress(n, result):
                        require(result['tuple'] == old['result']['tuple'], 'exact playback tuple differs')
                        run[n] = dict(tuple=result['tuple'], **result['r24Sweep'])
                        save()

                    lab.r24_progress = progress
                    result = lab.measure(name, archive['plannedPositions'])
                    require(len(result.get('r24Sweep', {}).get('positions', [])) == 24,
                            f"incomplete replay: {name}: {result.get('unmeasured', 'no sweep')}")
                matrix.cp.docker('stop', lab.cid)
                lab.inspect()
            finally:
                if lab.cid:
                    matrix.cp.docker('rm', '-f', '-v', lab.cid)
                shutil.rmtree(lab.work)
    out['complete'] = True
    save()


if __name__ == '__main__':
    main()
