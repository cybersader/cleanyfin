#!/usr/bin/env python3
"""Prove the hard fixture's analyzer detects leaks/losses and survives a lossy capture."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
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


def run(work):
    subprocess.check_output([sys.executable, str(HERE/'hard-fixture.py'), str(work)])
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
    print('\nColumns: verdict; marked/missing frames; leak ms; 440/660 Hz loss ms.')
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
