#!/usr/bin/env python3
"""Prove the hard fixture's analyzer detects leaks/losses and survives a lossy capture."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from exact_audio import (BYTES_PER_FRAME, compare_cut, marked_witness, plan_frames,
                         stereo_samples, summary)
from hard_fixture_lab import FPS, HZ, media, write_cut

HERE = Path(__file__).resolve().parent


def analyze(work, name):
    result = subprocess.run([sys.executable, str(HERE/'analyze-capture.py'),
                             '--plan', str(work/'plan.json'), '--video', str(work/name),
                             '--audio', str(work/name), '--json'], stdout=subprocess.PIPE, text=True)
    report = json.loads(result.stdout.splitlines()[-1])
    if 'error' in report:
        raise ValueError(f'{name}: analyzer error: {report["error"]}')
    if result.returncode != (0 if report['pass'] else 1):
        raise ValueError(f'{name}: exit status disagrees with analyzer verdict')
    return report


def check_exact_pcm(work):
    def decode(name):
        return media(work, 'ffmpeg', '-i', f'/lab/{name}', '-map', '0:a:0',
                     '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1', readonly=True)

    plan = json.loads((work/'identifiable.plan.json').read_text())
    start, end, total = plan_frames(plan)
    original = decode('identifiable.mp4')
    if len(original) != total*BYTES_PER_FRAME or len(set(stereo_samples(original))) != total:
        raise ValueError('lossless source does not carry a distinct stereo identity per sample')
    ref = decode('identifiable-reference.mkv')
    output_index, source_index = start-1, end-1
    out, marked = (i*BYTES_PER_FRAME for i in (output_index, source_index))
    replaced = ref[:out] + original[marked:marked+BYTES_PER_FRAME] + ref[out+BYTES_PER_FRAME:]
    cases = [('exact reference', ref, True, None, 0),
             ('one marked substitution', replaced, False, output_index, 0),
             ('one sample short', ref[:-BYTES_PER_FRAME], False, None, -1)]
    passed = True
    for label, pcm, exact, index, delta in cases:
        report = compare_cut(plan, original, pcm)
        witness = marked_witness(original, pcm, source_index)
        ok = (report['exact'] == exact and report['delta'] == delta
              and report['unequalFrames'] == int(index is not None)
              and report['firstUnequal'] == report['lastUnequal'] == witness == index)
        passed &= ok
        identity = f'; marked source {source_index} at output {witness}' if witness is not None else ''
        print(f'{"OK" if ok else "MISMATCH"}: {label}: {"PASS" if report["exact"] else "FAIL"}; '
              f'{summary(report)}{identity}', flush=True)
    return passed


def run(work):
    subprocess.check_output([sys.executable, str(HERE/'hard-fixture.py'), '--identifiable', str(work)])
    plan = json.loads((work/'plan.json').read_text())
    cut, = plan['cuts']
    start, end = cut['startFrame'], cut['endFrame']
    a_s, a_e, total = (i*HZ//FPS for i in (start, end, plan['source']['frames']))
    write_cut(work, 'undertrim.mkv', (start+1, end-1), (a_s+40*HZ//1000, a_e), total)
    write_cut(work, 'overtrim.mkv', (start-3, end+3), (a_s-3*HZ//FPS, a_e+3*HZ//FPS), total)
    media(work, 'ffmpeg', '-i', '/lab/reference.mkv', '-vf', 'scale=-2:480,gblur=sigma=1.2',
          '-c:v', 'libx264', '-preset', 'fast', '-threads:v', '4', '-crf', '30',
          '-c:a', 'copy', '/lab/lossy.mkv')

    cases = [
        ('Original', 'source.mp4', False, 126, [[137, 262]], [], 5040, 0),
        ('Reference', 'reference.mkv', True, 0, [], [], 0, 0),
        ('Under-trim', 'undertrim.mkv', False, 2, [[137, 137], [262, 262]], [], 40, 0),
        ('Over-trim', 'overtrim.mkv', False, 0, [], [[134, 136], [263, 265]], 0, 120),
        ('480p CRF 30 + blur', 'lossy.mkv', True, 0, [], [], 0, 0),
    ]
    print('| Case | Expected | Observed | Check |')
    print('|---|---|---|---|')
    passed = True
    for label, name, verdict, marked, marked_ranges, missing_ranges, leak, loss in cases:
        report = analyze(work, name)
        v, a = report['video'], report['audio']
        missing = sum(b-a+1 for a, b in missing_ranges)
        loss_440, loss_660 = (a['tonesHz'][f]['lossMs'] for f in ('440', '660'))
        actual_leak = a['leakMs']
        ok = (report['pass'] == verdict and v['markedCount'] == marked and v['markedRanges'] == marked_ranges
              and v['missingCount'] == missing and v['missingRanges'] == missing_ranges
              and v['unreadableCount'] == v['duplicateCount'] == 0 and v['monotonic']
              and abs(actual_leak-leak) <= (5 if leak else 0)
              and abs(loss_440-loss) <= 5 and abs(loss_660-loss) <= 5)
        expected = f'{"PASS" if verdict else "FAIL"}; {marked}/{missing}; {"~" if leak else ""}{leak}; {loss}/{loss}'
        observed = (f'{"PASS" if report["pass"] else "FAIL"}; {v["markedCount"]}/{v["missingCount"]}; '
                    f'{actual_leak:g}; {loss_440:g}/{loss_660:g}')
        print(f'| {label} | {expected} | {observed} | {"OK" if ok else "MISMATCH"} |', flush=True)
        if not ok:
            print(json.dumps(report), file=sys.stderr)
        passed &= ok
    print('\nColumns: verdict; marked/missing frames; leak ms; 440/660 Hz loss ms.', flush=True)
    passed &= check_exact_pcm(work)
    print(f'{"PASS" if passed else "FAIL"}: hard fixture kit self-test')
    return 0 if passed else 1


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        with tempfile.TemporaryDirectory(prefix='cleanyfin-hard-kit-') as directory:
            return run(Path(directory)/'fixture')
    except (OSError, ValueError, IndexError, subprocess.CalledProcessError) as exc:
        sys.exit(f'FAIL: {exc}')


if __name__ == '__main__':
    sys.exit(main())
