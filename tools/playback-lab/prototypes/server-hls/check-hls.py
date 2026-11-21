#!/usr/bin/env python3
"""Test boundary-only HLS packaging against fresh hard-fixture media."""
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from hls_lab import (FPS, HZ, LAB, ffmpeg, filtered_to_source, first_pts, playlist_parts,
                     probe, seek_options, source_to_filtered)

PROTOTYPE = Path(__file__).resolve().parent
PLAYLIST = 'stream/main.m3u8'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def python_script(path, *args, **kwargs):
    return subprocess.run([sys.executable, str(path), *(str(arg) for arg in args)],
                          text=True, **kwargs)


def check_mapping(start, end, source_duration):
    filtered_duration = source_duration - (end - start)
    prior = start - Fraction(1, FPS)
    step = Fraction(1, 7 * FPS)  # Resume positions need not be video frame boundaries.
    for position in (0, prior, start, start + step, filtered_duration):
        source_position = filtered_to_source(position, start, end)
        expected = position if position < start else position + end - start
        require(source_position == expected, f'wrong filtered-to-source mapping at {position}')
        require(source_to_filtered(source_position, start, end) == position, 'wrong inverse mapping')
    for position in (start, start + step, end - step, end):
        mapped = source_to_filtered(position, start, end)
        require(mapped == start and filtered_to_source(mapped, start, end) == end,
                f'inside-cut resume did not snap forward from {position} to {end}')
    require(filtered_to_source(filtered_duration, start, end) == source_duration, 'wrong endpoint')


def check_playlist(work, source_duration, start, end):
    expected = source_duration - (end - start)
    init, parts = playlist_parts(work/PLAYLIST)
    actual = sum(duration for _, duration in parts)
    require(abs(actual - expected) <= Fraction(1, FPS), 'VOD duration is off by more than one frame')
    require([d for _, d in parts] == [start, expected - start], 'HLS does not split at the splice')
    data = probe(work, PLAYLIST, '-select_streams', 'v:0', '-show_streams', '-show_packets',
                 '-show_entries', 'stream=time_base:packet=pts,flags')
    time_base = Fraction(data['streams'][0]['time_base'])
    timestamps = sorted(p['pts'] for p in data['packets'])
    require([(t - timestamps[0]) * time_base for t in timestamps]
            == [Fraction(i, FPS) for i in range(int(expected * FPS))],
            'HLS video timestamps do not follow the continuous filtered frame clock')
    key_times = {(p['pts'] - timestamps[0]) * time_base for p in data['packets'] if 'K' in p['flags']}
    require({0, start} <= key_times, 'segment boundaries are not keyframes')
    print(f'Filtered duration: {float(actual):.3f} s ({float(source_duration):.3f} s source)', flush=True)
    return init, parts


def capture(work, name, input_name, position=None):
    seek = seek_options(work, input_name, position) if position is not None else ()
    ffmpeg(work, *seek, '-i', f'/lab/{input_name}', '-map', '0:v:0', '-map', '0:a:0',
           '-c:v', 'ffv1', '-threads:v', '4', '-fps_mode', 'passthrough',
           '-c:a', 'pcm_s16le', '-ar', str(HZ), '-ac', '1', f'/lab/{name}.mkv')
    return name + '.mkv'


def pcm(work, capture_name):
    return ffmpeg(work, '-i', f'/lab/{capture_name}', '-map', '0:a:0',
                  '-c:a', 'pcm_s16le', '-f', 's16le', 'pipe:1', readonly=True)


def assemble_video(work, output, inputs):
    # Join *decoded* presentations without extending either input to its audio
    # duration. Set new PTS; preserve every input frame in presentation order.
    args, chains, labels = [], [], []
    for index, (name, limit) in enumerate(inputs):
        args += ['-i', f'/lab/{name}']
        trim = f'trim=end_frame={limit},' if limit is not None else ''
        labels.append(f'[v{index}]')
        chains.append(f'[{index}:v]{trim}settb=1/{FPS},setpts=N{labels[-1]}')
    chains.append(''.join(labels) + f'concat=n={len(inputs)}:v=1:a=0,settb=1/{FPS},setpts=N[out]')
    ffmpeg(work, *args, '-filter_complex', ';'.join(chains), '-map', '[out]', '-c:v', 'ffv1',
           '-threads:v', '4', '-fps_mode', 'passthrough', f'/lab/{output}')
    return output


def score(work, plan, label, video, audio, frames, seek_audio_boundary=None):
    result = python_script(LAB/'analyze-capture.py', '--plan', plan, '--video', work/video,
                           '--audio', work/audio, '--json', stdout=subprocess.PIPE)
    rows = result.stdout.splitlines()
    report = json.loads(rows[-1])
    print(f'boundary-only | {label} | {rows[-2]}', flush=True)
    if result.returncode or not report['pass']:
        print('\n'.join(rows[:-2]), file=sys.stderr)
        return False
    v = report['video']
    require(v['frames'] == frames and v['duplicateCount'] == 0, f'{label}: wrong exact frame count')
    if seek_audio_boundary is not None:
        # The reconstructed seek may not repeat allowed pre-splice audio after
        # the splice. The scorer quantizes detections to its 5 ms windows.
        a = report['audio']
        require(all(last <= seek_audio_boundary * 1000 + a['windowMs']
                    for _, last in a['tonesHz']['440']['captureRangesMs']),
                f'{label}: pre-cut audio persisted after the seek splice')
    return True


def check(work):
    fixture = work/'fixture'
    python_script(LAB/'hard-fixture.py', fixture, check=True)
    plan_path = fixture/'plan.json'
    plan = json.loads(plan_path.read_text())
    cut, = plan['cuts']
    source_frames = plan['source']['frames']
    start, end = (Fraction(cut[field], 1000) for field in ('startMs', 'endMs'))
    source_duration = Fraction(source_frames, FPS)
    check_mapping(start, end, source_duration)
    out = work/'stream'
    result = python_script(PROTOTYPE/'package-hls.py', '--source', fixture/'source.mp4',
                           '--plan', plan_path, '--out', out, check=True, stdout=subprocess.PIPE)
    stats = json.loads(result.stdout)
    print(f'{stats["strategy"]}: {stats["streamCopiedFrames"]} stream-copied, '
          f'{stats["reencodedFrames"]} re-encoded video frames; '
          f'{stats["packagingSeconds"]:.3f} s wall time; {stats["format"]}', flush=True)
    require((stats['streamCopiedFrames'], stats['reencodedFrames']) == (300, 74),
            'hard fixture must copy six GOPs and re-encode only two partial GOPs')
    init, segments = check_playlist(work, source_duration, start, end)
    frames = int((source_duration - end + start) * FPS)
    passed = []

    direct = capture(work, 'direct', PLAYLIST)
    direct_pcm = pcm(work, direct)
    passed.append(score(work, plan_path, 'playlist', direct, direct, frames))

    decoded = []
    for index, (uri, _) in enumerate(segments):
        if init:
            # An fMP4 fragment is independently decodable with its advertised init.
            source = f'with-init-{index}.mp4'
            with (work/source).open('wb') as dest:
                for file in (init, uri):
                    with (out/file).open('rb') as src:
                        shutil.copyfileobj(src, dest)
        else:
            source = f'stream/{uri}'
        decoded.append(capture(work, f'fragment-{index}', source))
    video = assemble_video(work, 'independent.mkv', [(name, None) for name in decoded])
    audio = b''.join(pcm(work, name) for name in decoded)
    require(len(audio) == len(direct_pcm), 'independent segments changed the decoded sample count')
    (work/'independent.pcm').write_bytes(audio)
    passed.append(score(work, plan_path, 'segments independently', video, 'independent.pcm', frames))

    priming = first_pts(work, PLAYLIST, 'v:0') - first_pts(work, PLAYLIST, 'a:0')
    require(0 <= priming <= Fraction(1, FPS), 'unexpected amount of audio preceding video zero')
    for label, position in (('seek one second before splice', start - 1), ('seek at splice', start)):
        prefix_frames = int(position * FPS)
        prefix_samples = round((priming + position) * HZ)
        suffix = capture(work, 'seek', PLAYLIST, position)
        # The unmodified scorer expects a full fixture. Supply only the already
        # verified prefix, and append all output from the independent seek.
        # No seek frames or audio samples are trimmed, padded, or replaced.
        video = assemble_video(work, 'seek-complete.mkv', [(direct, prefix_frames), (suffix, None)])
        completed_pcm = direct_pcm[:prefix_samples * 2] + pcm(work, suffix)
        require(abs(len(completed_pcm) - len(direct_pcm)) <= 2 * HZ * 5 // 1000,
                f'{label}: seek audio sample count differs by more than 5 ms')
        (work/'seek-complete.pcm').write_bytes(completed_pcm)
        passed.append(score(work, plan_path, label, video, 'seek-complete.pcm', frames, priming + start))
        for file in (suffix, video):
            (work/file).unlink()

    plan['source']['sha256'] = '0' * 64
    bad_plan, bad_out = work/'bad-plan.json', work/'must-not-publish'
    bad_plan.write_text(json.dumps(plan))
    bad = python_script(PROTOTYPE/'package-hls.py', '--source', fixture/'source.mp4',
                        '--plan', bad_plan, '--out', bad_out, capture_output=True)
    require(bad.returncode != 0 and 'does not match' in bad.stderr and not bad_out.exists(),
            'wrong-source plan did not fail closed')
    return all(passed)


def main():
    try:
        with tempfile.TemporaryDirectory(prefix='cleanyfin-hls-check-') as directory:
            passed = check(Path(directory).resolve())
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        sys.exit(f'FAIL: boundary-only HLS: {exc}')
    if not passed:
        sys.exit('FAIL: boundary-only HLS')
    print('PASS: boundary-only HLS, resume mapping, and wrong-source fail-closed check')


if __name__ == '__main__':
    main()
