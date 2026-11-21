#!/usr/bin/env python3
"""Measure frame and tone leaks/losses in captures of the hard playback fixture."""
import argparse
from array import array
from collections import Counter
import json
import math
from operator import mul
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
from hard_fixture_lab import BITS, CELL, FPS, HZ, SCAN_HEIGHT, SCAN_WIDTH, SYNC, X, Y, media, ranges

TONES = (440, 880, 660)
WINDOW_MS = 5
WINDOW = HZ*WINDOW_MS//1000
# -20 dBFS peak: above the reference's splice/edge transients at any window phase,
# below the nominal 0.25 peak of each fixture tone.
PEAK_THRESHOLD = 0.10
IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.ppm', '.pgm'}


def read_plan(path):
    plan = json.loads(path.read_text())
    if (type(plan['version']) is not int or plan['version'] != 0
            or len(plan['cuts']) != 1):
        raise ValueError('expected the single-cut lab plan v0')
    source, cut = plan['source'], plan['cuts'][0]
    frames, start, end = source['frames'], cut['startFrame'], cut['endFrame']
    if (not all(type(n) is int for n in (frames, start, end))
            or not 0 < start < end < frames <= 1 << BITS
            or (source['fps'], source['sampleRate']) != (FPS, HZ)
            or (cut['startMs'], cut['endMs']) != (start*1000//FPS, end*1000//FPS)
            or not re.fullmatch(r'[0-9a-f]{64}', source['sha256'])):
        raise ValueError('invalid source or frame boundaries in lab plan')
    return frames, start, end


def image_manifest(directory):
    files = []
    for file in directory.iterdir():
        if not file.is_file() or file.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        number = re.search(r'\d+$', file.stem)
        if not number or '\n' in file.name or '\r' in file.name or file.is_symlink():
            raise ValueError('dumped frames must have numbered, regular image filenames')
        files.append((int(number[0]), file.name))
    if not files or len({i for i, _ in files}) != len(files):
        raise ValueError('expected a nonempty image dump with unique frame numbers')
    if len({Path(name).suffix.lower().replace('.jpeg', '.jpg') for _, name in files}) != 1:
        raise ValueError('use one image format per frame dump')
    # Local concat manifest; filenames are escaped and ordered numerically, not lexically.
    return ('ffconcat version 1.0\n' + ''.join(
        "file 'file:/lab/" + name.replace("'", "'\\''") + "'\n" for _, name in sorted(files))).encode()


def read_barcode(pixels, frames):
    cells = []
    for row in range(2):
        values = []
        y = (Y+row*CELL+CELL//2)//8
        for col in range(len(SYNC) + BITS):
            x = (X+col*CELL+CELL//2)//8
            values.append(sum(sum(pixels[i*SCAN_WIDTH+x-2:i*SCAN_WIDTH+x+2])
                              for i in range(y-2, y+2)) / 16)
        cells.append(values)
    by_sync = [(cells[row][i], bit ^ row) for row in range(2) for i, bit in enumerate(SYNC)]
    dark = max(value for value, bit in by_sync if not bit)
    light = min(value for value, bit in by_sync if bit)
    gap = light - dark
    if gap < 96:
        return None
    bits = [[0 if v <= dark+gap/3 else 1 if v >= light-gap/3 else None for v in row] for row in cells]
    if any(a is None or b is None or a == b for a, b in zip(*bits)):
        return None
    number = sum(bit << i for i, bit in enumerate(bits[0][len(SYNC):]))
    return number if number < frames else None


def analyze_video(path, total, start, end):
    if path.is_dir():
        data = image_manifest(path)
        input_args = ['-f', 'concat', '-safe', '0', '-i', 'pipe:0']
    elif path.is_file():
        data, input_args = None, ['-i', '/lab']
    else:
        raise ValueError(f'video file or frame directory not found: {path}')
    # Keep every decoded frame in presentation order. Only the raw pipe's synthetic
    # PTS are replaced, since timestamp-less frame dumps can repeat or omit PTS.
    filters = f'scale={SCAN_WIDTH}:{SCAN_HEIGHT}:flags=area,format=gray,settb=1/{FPS},setpts=N'
    raw = memoryview(media(path, 'ffmpeg', '-protocol_whitelist', 'file,pipe', *input_args, '-map', '0:v:0',
                           '-vf', filters, '-fps_mode', 'passthrough', '-enc_time_base:v', 'filter',
                           '-threads:v', '1', '-f', 'rawvideo', 'pipe:1', readonly=True, data=data))
    size = SCAN_WIDTH*SCAN_HEIGHT
    if len(raw) % size:
        raise ValueError('incomplete raw video frame')
    present = []
    unreadable = 0
    for offset in range(0, len(raw), size):
        frame = read_barcode(raw[offset:offset+size], total)
        if frame is None:
            unreadable += 1
        else:
            present.append(frame)
    counts = Counter(present)
    marked = [i for i in counts if start <= i < end]
    missing = (set(range(start)) | set(range(end, total))) - counts.keys()
    duplicates = [i for i, count in counts.items() if count > 1]
    monotonic = all(a <= b for a, b in zip(present, present[1:]))
    return {'frames': len(raw)//size, 'markedCount': sum(counts[i] for i in marked),
            'markedRanges': ranges(marked), 'missingCount': len(missing), 'missingRanges': ranges(missing),
            'unreadableCount': unreadable, 'duplicateCount': sum(counts[i]-1 for i in duplicates),
            'duplicateRanges': ranges(duplicates), 'monotonic': monotonic,
            'pass': not (marked or missing or unreadable) and monotonic}


def tone_basis():
    # Fit DC and all three tones at once. Simple independent Goertzel bins bleed
    # into each other over a short window, especially when two tones overlap.
    columns = [[1.0]*WINDOW] + [[wave(2*math.pi*f*i/HZ) for i in range(WINDOW)]
                               for f in TONES for wave in (math.cos, math.sin)]
    # Modified Gram-Schmidt QR; this basis stays the same for every window.
    q = []
    r = [[0.0]*len(columns) for _ in columns]
    for j, column in enumerate(columns):
        for i, axis in enumerate(q):
            r[i][j] = sum(map(mul, axis, column))
            column = [a-r[i][j]*b for a, b in zip(column, axis)]
        r[j][j] = math.sqrt(sum(map(mul, column, column)))
        q.append([a/r[j][j] for a in column])
    return q, r


def analyze_audio(path, total, start, end):
    if not path.is_file():
        raise ValueError(f'audio file not found: {path}')
    input_args = ['-f', 's16le', '-ar', str(HZ), '-ac', '1'] if path.suffix.lower() in {'.pcm', '.s16le'} else []
    raw = media(path, 'ffmpeg', '-protocol_whitelist', 'file,pipe', *input_args, '-i', '/lab',
                '-map', '0:a:0', '-ar', str(HZ), '-ac', '1', '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1',
                readonly=True)
    samples = array('h', raw)
    if sys.byteorder != 'little':
        samples.byteswap()
    capture_ms = len(samples)*1000/HZ
    q, r = tone_basis()
    detections = {f: [] for f in TONES}
    for offset in range(0, len(samples), WINDOW):
        block = samples[offset:offset+WINDOW]  # Implicit zero padding in dot products for a partial last window.
        c = [sum(map(mul, axis, block))/32768 for axis in q]
        for i in range(len(c)-1, -1, -1):
            c[i] = (c[i]-sum(r[i][j]*c[j] for j in range(i+1, len(c))))/r[i][i]
        for i, f in enumerate(TONES):
            if math.hypot(c[2*i+1], c[2*i+2]) >= PEAK_THRESHOLD:
                detections[f].append(offset//WINDOW)
    expected = {440: start*1000//FPS, 880: 0, 660: (total-end)*1000//FPS}
    tones = {}
    for f in TONES:
        intervals = [[a*WINDOW_MS, min((b+1)*WINDOW_MS, capture_ms)] for a, b in ranges(detections[f])]
        present = sum(b-a for a, b in intervals)
        tones[str(f)] = {'presentMs': present, 'expectedMs': expected[f],
                         'lossMs': max(0, expected[f]-present), 'captureRangesMs': intervals}
    leak = tones['880']['presentMs']
    return {'windowMs': WINDOW_MS, 'thresholdPeak': PEAK_THRESHOLD, 'captureMs': capture_ms,
            'leakMs': leak, 'tonesHz': tones,
            'pass': not leak and all(tones[f]['lossMs'] <= WINDOW_MS for f in ('440', '660'))}


def format_ranges(runs):
    return ', '.join(str(a) if a == b else f'{a}–{b}' for a, b in runs) or 'none'


def describe(result):
    if 'video' in result:
        v = result['video']
        for label, key, range_key in (('Marked presentations', 'markedCount', 'markedRanges'),
                                       ('Missing unmarked frames', 'missingCount', 'missingRanges'),
                                       ('Extra duplicate presentations', 'duplicateCount', 'duplicateRanges')):
            print(f'{label}: {v[key]} ({format_ranges(v[range_key])})')
        print(f'Unreadable frames: {v["unreadableCount"]}; source order monotonic: {v["monotonic"]}')
    if 'audio' in result:
        a = result['audio']
        print(f'880 Hz leak: {a["leakMs"]:g} ms (peak ≥ {PEAK_THRESHOLD} full scale, {WINDOW_MS} ms windows)')
        for f, tone in a['tonesHz'].items():
            intervals = ', '.join(f'[{start:g}, {end:g})' for start, end in tone['captureRangesMs']) or 'none'
            print(f'{f} Hz: {tone["presentMs"]:g} / {tone["expectedMs"]:g} ms expected; '
                  f'loss {tone["lossMs"]:g} ms; capture ms {intervals}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--video', type=Path, metavar='FILE_OR_FRAME_DIRECTORY')
    parser.add_argument('--audio', type=Path, metavar='FILE')
    parser.add_argument('--json', action='store_true', help='also print JSON on the last line')
    args = parser.parse_args()
    if not (args.video or args.audio):
        parser.error('give --video, --audio, or both')
    result = {'pass': False}
    try:
        boundaries = read_plan(args.plan)
        if args.video:
            result['video'] = analyze_video(args.video.resolve(), *boundaries)
        if args.audio:
            result['audio'] = analyze_audio(args.audio.resolve(), *boundaries)
        result['pass'] = all(m['pass'] for media_type, m in result.items() if media_type != 'pass')
        describe(result)
        print(f'{"PASS" if result["pass"] else "FAIL"}: {" and ".join(k for k in ("video", "audio") if k in result)}')
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        result.update({'pass': False, 'error': str(exc)})
        print(f'FAIL: {exc}')
    if args.json:
        print(json.dumps(result))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
