#!/usr/bin/env python3
"""Generate the shared mid-GOP, mid-AAC-packet playback lab in a new directory."""
import argparse
import hashlib
import json
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

sys.dont_write_bytecode = True
from hard_fixture_lab import (BITS, CELL, FPS, HEIGHT, HZ, IDENTIFIABLE_CUT, SYNC,
                              WIDTH, X, Y, media, write_cut, write_identifiable_movie)

FRAMES, GOP = 500, 50
START, END = 137, 263  # Half-open; source frame 262 is the last marked frame.
AAC_SAMPLES = 1024


def barcode_filter():
    filters = [
        f'color=c=blue:s={WIDTH}x{HEIGHT}:r={FPS}:d={FRAMES//FPS}',
        f"drawbox=color=red:t=fill:enable='gte(n,{START})*lt(n,{END})'",
        f"drawbox=color=green:t=fill:enable='gte(n,{END})'",
        f'drawbox=x={X}:y={Y}:w={CELL*(len(SYNC)+BITS)}:h={CELL*2}:color=black:t=fill',
    ]
    for row in range(2):  # Second row is the complement, to reject damaged frames.
        for column in range(len(SYNC) + BITS):
            box = f'drawbox=x={X+column*CELL}:y={Y+row*CELL}:w={CELL}:h={CELL}:color=white:t=fill'
            if column < len(SYNC):
                if SYNC[column] ^ row:
                    filters.append(box)
            else:
                mask = 1 << (column - len(SYNC))
                bit = f'bitand(n,{mask})'
                filters.append(f"{box}:enable='{bit if row == 0 else f'not({bit})'}'")
    return ','.join(filters)


def probe(out, *args):
    return json.loads(media(out, 'ffprobe', *args, '-of', 'json', '/lab/source.mp4', readonly=True))


def write_edit_lists(plan_path):
    plan = json.loads(plan_path.read_text())
    cut, = plan['cuts']
    start, end = cut['startMs'], cut['endMs']
    duration = plan['source']['frames']*1000//plan['source']['fps']

    def seconds(ms):
        return f'{ms//1000}.{ms%1000:03}'

    plan_path.with_name('fixture.edl').write_text(f'{seconds(start)}\t{seconds(end)}\t0\n')
    plan_path.with_name('fixture.mpv.edl').write_text(
        f'# mpv EDL v0\nsource.mp4,0,{seconds(start)}\n'
        f'source.mp4,{seconds(end)},{seconds(duration-end)}\n')


def generate(out, *, source_only=False):
    start_sample, end_sample = (i*HZ//FPS for i in (START, END))
    if HZ % FPS or any(i % GOP == 0 for i in (START, END)) or any(
            i % AAC_SAMPLES == 0 for i in (start_sample, end_sample)):
        raise ValueError('cut must be on exact samples, inside GOPs and inside AAC packets')
    source_samples = FRAMES*HZ//FPS
    # n is the integer sample index, so neither transition depends on a float timestamp.
    tone = (f"0.25*sin(2*PI*if(lt(n,{start_sample}),440*n,"
            f"if(lt(n,{end_sample}),880*(n-{start_sample}),660*(n-{end_sample})))/{HZ})")
    media(out, 'ffmpeg', '-f', 'lavfi', '-i', barcode_filter(), '-f', 'lavfi', '-i',
          f"aevalsrc='{tone}|{tone}':s={HZ}:c=stereo:d={FRAMES//FPS}",
          '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p',
          '-profile:v', 'high', '-g', str(GOP), '-bf', '3', '-sc_threshold', '0',
          '-x264-params', 'b-adapt=0:open-gop=0', '-threads:v', '4', '-c:a', 'aac', '-profile:a', 'aac_low', '-b:a', '192k',
          '-movflags', '+faststart', '/lab/source.mp4')
    video = probe(out, '-select_streams', 'v:0', '-show_streams', '-show_frames', '-show_entries',
                  'stream=time_base,profile,width,height:frame=pts,pts_time,key_frame,pict_type')
    frames, stream = video['frames'], video['streams'][0]
    base = Fraction(stream['time_base'])
    keys = [i for i, f in enumerate(frames) if f['key_frame']]
    if (len(frames) != FRAMES or keys != list(range(0, FRAMES, GOP))
            or not any(f['pict_type'] == 'B' for f in frames)
            or (stream['profile'], stream['width'], stream['height']) != ('High', WIDTH, HEIGHT)
            or any((f['pts']-frames[0]['pts'])*base != Fraction(i, FPS) for i, f in enumerate(frames))):
        raise ValueError(f'unexpected muxed video layout: {stream}, {len(frames)} frames, '
                         f'keys {keys}, picture types {sorted({f["pict_type"] for f in frames})}')
    audio = probe(out, '-select_streams', 'a:0', '-show_streams', '-show_packets', '-show_entries',
                  'stream=profile,sample_rate,channels,time_base,duration_ts:packet=pts,duration')
    a = audio['streams'][0]
    if (a['profile'], int(a['sample_rate']), a['channels'], Fraction(a['time_base']), a['duration_ts']) != (
            'LC', HZ, 2, Fraction(1, HZ), source_samples) or not all(
            any(p['pts'] < boundary < p['pts']+p['duration'] for p in audio['packets'])
            for boundary in (start_sample, end_sample)):
        raise ValueError('muxed source does not have the expected stereo AAC-LC or mid-packet cut')
    digest = hashlib.sha256((out/'source.mp4').read_bytes()).hexdigest()
    cut = {'startFrame': START, 'endFrame': END, 'startMs': START*1000//FPS, 'endMs': END*1000//FPS,
           'muxedVideo': {'timeBase': stream['time_base'],
                          'firstMarkedPts': frames[START]['pts'], 'firstMarkedPtsTime': frames[START]['pts_time'],
                          'lastMarkedPts': frames[END-1]['pts'], 'lastMarkedPtsTime': frames[END-1]['pts_time']}}
    plan_path = out/'plan.json'
    plan_path.write_text(json.dumps({'version': 0, 'source': {'sha256': digest, 'frames': len(frames),
                     'fps': FPS, 'sampleRate': HZ}, 'cuts': [cut]}, indent=2) + '\n')
    if not source_only:
        write_cut(out, 'reference.mkv', (START, END), (start_sample, end_sample), source_samples)
        write_edit_lists(plan_path)


def generate_identifiable(out):
    source_samples = FRAMES*HZ//FPS
    write_identifiable_movie(out, source_samples)
    start, end = IDENTIFIABLE_CUT
    variant = 'identifiable.mp4'
    digest = hashlib.sha256((out/variant).read_bytes()).hexdigest()
    plan = {'version': 0, 'source': {'sha256': digest, 'frames': FRAMES, 'fps': FPS, 'sampleRate': HZ},
            'cuts': [{'startFrame': start, 'endFrame': end,
                      'startMs': start*1000//FPS, 'endMs': end*1000//FPS}]}
    (out/'identifiable.plan.json').write_text(json.dumps(plan, indent=2) + '\n')
    write_cut(out, 'identifiable-reference.mkv', (start, end), (start*HZ//FPS, end*HZ//FPS),
              source_samples, source=variant)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, metavar='NEW_DIRECTORY')
    parser.add_argument('--source-only', action='store_true', help='write only the original and its plan, with no cut media')
    parser.add_argument('--identifiable', action='store_true', help='also create the lossless sample-ID variant')
    parser_args = parser.parse_args()
    if parser_args.source_only and parser_args.identifiable:
        parser.error('--source-only and --identifiable are mutually exclusive')
    out = parser_args.directory.resolve()
    try:
        out.mkdir(mode=0o700, exist_ok=False)
        generate(out, source_only=parser_args.source_only)
        if parser_args.identifiable:
            generate_identifiable(out)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'FAIL: {exc}\n')
    print(out)


if __name__ == '__main__':
    main()
