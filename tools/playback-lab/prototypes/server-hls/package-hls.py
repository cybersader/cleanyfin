#!/usr/bin/env python3
"""Package the single-cut hard fixture as a filtered VOD HLS stream."""
import argparse
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from math import ceil
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
from hls_lab import FPS, HZ, ffmpeg, playlist_parts, probe, seconds, seek_options


@dataclass(frozen=True)
class Cut:
    frames: int
    start: int
    end: int
    start_time: Fraction
    end_time: Fraction

    @property
    def duration(self):
        return Fraction(self.frames, FPS) - (self.end_time - self.start_time)


def video_packets(work, name):
    data = probe(work, name, '-select_streams', 'v:0', '-show_streams', '-show_packets',
                 '-show_data_hash', 'sha256', '-show_entries',
                 'stream=codec_name,codec_tag_string,profile,level,width,height,pix_fmt,time_base,'
                 'extradata_hash,nal_length_size:packet=pts,dts,duration,flags,pos,size,data_hash')
    stream, = data['streams']
    return stream, data['packets']


def regular_frames(packets, time_base, frames):
    return (len(packets) == frames
            and sorted(p['pts'] * time_base for p in packets)
            == [Fraction(i, FPS) for i in range(frames)])


def assert_idr(file, packet, nal_length_size):
    # A packet's key flag alone does not prove the following pictures can be
    # decoded without references to a previous GOP. Require an AVC IDR.
    file.seek(int(packet['pos']))
    data = file.read(int(packet['size']))
    offset = 0
    while offset + nal_length_size < len(data):
        size = int.from_bytes(data[offset:offset + nal_length_size], 'big')
        offset += nal_length_size
        if not size or offset + size > len(data):
            break
        kind = data[offset] & 31
        if 1 <= kind <= 5:
            if kind == 5:
                return
            break
        offset += size
    raise ValueError('keyframe is not an independently decodable AVC IDR')


def inspect_source(work, plan):
    source, cut_list = plan['source'], plan['cuts']
    cut, = cut_list
    frames, start, end = source['frames'], cut['startFrame'], cut['endFrame']
    if (type(plan['version']) is not int or plan['version'] != 0
            or not all(type(n) is int for n in (frames, start, end))
            or not FPS <= start < end < frames <= 512
            or (source['fps'], source['sampleRate']) != (FPS, HZ)):
        raise ValueError('expected the supported single-cut hard-fixture lab plan v0')
    with (work/'source.mp4').open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() != source['sha256']:
            raise ValueError('source SHA-256 does not match the cut plan')

    stream, packets = video_packets(work, 'source.mp4')
    time_base = Fraction(stream['time_base'])
    if ((stream['codec_name'], stream['codec_tag_string'], stream['profile'],
         stream['width'], stream['height'], stream['pix_fmt'])
            != ('h264', 'avc1', 'High', 1280, 720, 'yuv420p')
            or not regular_frames(packets, time_base, frames)):
        raise ValueError('unsupported source video layout')
    pts = sorted(p['pts'] for p in packets)
    muxed = cut['muxedVideo']
    start_time, end_time = pts[start] * time_base, pts[end] * time_base
    if (Fraction(muxed['timeBase']) != time_base
            or (muxed['firstMarkedPts'], muxed['lastMarkedPts']) != (pts[start], pts[end-1])
            or Fraction(muxed['firstMarkedPtsTime']) != start_time
            or Fraction(muxed['lastMarkedPtsTime']) != pts[end-1] * time_base
            or (cut['startMs'], cut['endMs']) != (start_time * 1000, end_time * 1000)):
        raise ValueError('cut plan does not match muxed video timestamps')

    audio, = probe(work, 'source.mp4', '-select_streams', 'a', '-show_streams', '-show_entries',
                   'stream=codec_name,profile,channels,sample_rate,time_base,start_pts,duration_ts')['streams']
    if ((audio['codec_name'], audio['profile'], audio['channels'], int(audio['sample_rate']),
         Fraction(audio['time_base']), audio['start_pts'], audio['duration_ts'])
            != ('aac', 'LC', 2, HZ, Fraction(1, HZ), 0, frames * HZ // FPS)):
        raise ValueError('unsupported source audio layout')

    nal_size = int(stream['nal_length_size'])
    if nal_size not in (1, 2, 4):
        raise ValueError('unsupported AVC NAL length size')
    keys = []
    with (work/'source.mp4').open('rb') as file:
        for index, packet in enumerate(packets):
            if 'K' in packet['flags']:
                assert_idr(file, packet, nal_size)
                keys.append(index)
    if not keys or keys[0] != 0:
        raise ValueError('source does not start with an IDR')
    gops = list(zip(keys, keys[1:] + [frames]))
    for lo, hi in gops:
        if sorted(p['pts'] * time_base for p in packets[lo:hi]) != [
                Fraction(i, FPS) for i in range(lo, hi)]:
            raise ValueError('a GOP crosses a presentation boundary')
    if any(hi - lo != 50 for lo, hi in gops):
        raise ValueError('expected the hard fixture’s fixed 50-frame GOPs')
    return Cut(frames, start, end, start_time, end_time), stream, packets, gops


def select_chunks(cut, gops):
    """(start, end, copy, decode_start), in filtered presentation order."""
    chunks = []
    for keep_lo, keep_hi in ((0, cut.start), (cut.end, cut.frames)):
        for lo, hi in gops:
            start, end = max(lo, keep_lo), min(hi, keep_hi)
            if start >= end:
                continue
            copy = (start, end) == (lo, hi)
            if copy and chunks and chunks[-1][2] and chunks[-1][1] == start:
                last, _, _, decode = chunks[-1]
                chunks[-1] = (last, end, True, decode)
            else:
                chunks.append((start, end, copy, lo))
    return chunks


def make_chunks(work, cut, source_stream, source_packets, gops):
    manifest = ['ffconcat version 1.0']
    copied = encoded = 0
    for index, (start, end, copy, decode_start) in enumerate(select_chunks(cut, gops)):
        name = f'chunk-{index:02d}.mp4'
        count, skip = end - start, start - decode_start
        options = ['-c:v', 'copy'] if copy else [
            '-vf', f'trim=start_frame={skip}:end_frame={skip + count},setpts=PTS-STARTPTS',
            '-fps_mode', 'passthrough', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
            '-pix_fmt', 'yuv420p', '-profile:v', 'high', '-g', '50', '-bf', '3',
            '-sc_threshold', '0', '-x264-params', 'b-adapt=0:open-gop=0', '-threads:v', '4',
        ]
        ffmpeg(work, '-ss', seconds(Fraction(decode_start, FPS)), '-threads:v', '1',
               '-i', '/lab/source.mp4', '-map', '0:v:0', '-map_metadata', '-1', '-frames:v', str(count),
               *options, f'/lab/{name}')
        stream, packets = video_packets(work, name)
        if (stream['extradata_hash'] != source_stream['extradata_hash']
                or stream['time_base'] != source_stream['time_base']):
            raise ValueError('boundary AVC configuration differs; this prototype cannot join it')
        if (not regular_frames(packets, Fraction(stream['time_base']), count)
                or 'K' not in packets[0]['flags']):
            raise ValueError(f'invalid chunk timeline: {name}')
        with (work/name).open('rb') as file:
            assert_idr(file, packets[0], int(stream['nal_length_size']))
        if copy:
            if [p['data_hash'] for p in packets] != [p['data_hash'] for p in source_packets[start:end]]:
                raise ValueError(f'stream-copied GOP packet mismatch: {name}')
            copied += count
        else:
            encoded += count
        manifest += [f'file {name}', f'duration {seconds(Fraction(count, FPS))}']
    if copied + encoded != cut.duration * FPS:
        raise ValueError('chunk counts do not agree with filtered timeline')
    (work/'chunks.ffconcat').write_text('\n'.join(manifest) + '\n')
    return copied, encoded


def make_hls(work, cut, kind):
    start, end = (int(t * HZ) for t in (cut.start_time, cut.end_time))
    audio = (f'[1:a]asplit=2[a0][a1];[a0]atrim=end_sample={start},asetpts=PTS-STARTPTS[p];'
             f'[a1]atrim=start_sample={end}:end_sample={cut.frames * HZ // FPS},'
             'asetpts=PTS-STARTPTS[q];[p][q]concat=n=2:v=0:a=1,asetpts=N/SR/TB[a]')
    ext = 'm4s' if kind == 'fmp4' else 'ts'
    work.joinpath(kind).mkdir()
    options = ('-hls_fmp4_init_filename', 'init.mp4') if kind == 'fmp4' else ()
    ffmpeg(work, '-f', 'concat', '-auto_convert', '0', '-i', '/lab/chunks.ffconcat',
           '-i', '/lab/source.mp4', '-filter_complex', audio, '-map', '0:v:0', '-map', '[a]',
           '-c:v', 'copy', '-c:a', 'aac', '-profile:a', 'aac_low', '-b:a', '192k',
           '-hls_time', seconds(cut.start_time), '-hls_list_size', '0', '-hls_flags', 'independent_segments',
           '-hls_playlist_type', 'vod', '-hls_segment_type', kind, *options,
           '-hls_segment_filename', f'/lab/{kind}/native-%03d.{ext}', f'/lab/{kind}/native.m3u8')
    return group_at_splice(work/kind, cut, ext)


def group_at_splice(directory, cut, ext):
    # Publish one independent segment per kept range. The native HLS muxer
    # assigns continuous fMP4 decode times or TS timestamps and counters.
    # Grouping (without remuxing) also avoids unnecessary AAC decoder resets.
    init, native = playlist_parts(directory/'native.m3u8')
    if native[0][1] != cut.start_time or sum(d for _, d in native) != cut.duration:
        raise ValueError('HLS muxer did not segment on the exact splice and end')
    durations = (cut.start_time, cut.duration - cut.start_time)
    tags = ['#EXTM3U', f'#EXT-X-VERSION:{7 if init else 6}',
            f'#EXT-X-TARGETDURATION:{ceil(max(durations))}',
            '#EXT-X-MEDIA-SEQUENCE:0', '#EXT-X-PLAYLIST-TYPE:VOD', '#EXT-X-INDEPENDENT-SEGMENTS']
    if init:
        tags.append(f'#EXT-X-MAP:URI="{init}"')
    for i, (group, duration) in enumerate(zip((native[:1], native[1:]), durations)):
        segment = f'segment-{i:03d}.{ext}'
        with (directory/segment).open('wb') as dest:
            for name, _ in group:
                with (directory/name).open('rb') as source:
                    shutil.copyfileobj(source, dest)
        tags += [f'#EXTINF:{seconds(duration)},', segment]
    playlist = directory/'main.m3u8'
    playlist.write_text('\n'.join(tags + ['#EXT-X-ENDLIST', '']))
    return playlist


def seek_failures(work, playlist, cut):
    name = str(playlist.relative_to(work))
    failures = []
    for position in (cut.start_time - 1, cut.start_time):
        output = ffmpeg(work, *seek_options(work, name, position), '-i', f'/lab/{name}',
                        '-map', '0:v:0', '-fps_mode', 'passthrough', '-frames:v', '1',
                        '-threads:v', '1', '-f', 'framecrc', 'pipe:1', readonly=True)
        rows = [line.split(b',') for line in output.splitlines() if line and not line.startswith(b'#')]
        if not rows:
            failures.append(f'{seconds(position)} s decoded zero video frames')
        else:
            # framecrc reports timestamps relative to the requested presentation time.
            time_base, = (line.split()[-1] for line in output.splitlines() if line.startswith(b'#tb 0:'))
            delay = int(rows[0][2]) * Fraction(time_base.decode())
            if delay:
                failures.append(f'{seconds(position)} s landed {seconds(delay)} s off target')
    return failures


def package(source, plan_path, out):
    started = time.monotonic()
    out.mkdir(mode=0o700, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix='cleanyfin-hls-package-') as tmp:
            work = Path(tmp).resolve()
            shutil.copyfile(source, work/'source.mp4')
            cut, stream, packets, gops = inspect_source(work, json.loads(plan_path.read_text()))
            copied, encoded = make_chunks(work, cut, stream, packets, gops)
            kind = 'fmp4'
            playlist = make_hls(work, cut, kind)
            failures = seek_failures(work, playlist, cut)
            fallback = None
            if failures:
                fallback = 'fMP4 input seek: ' + '; '.join(failures)
                print(f'{fallback}; trying MPEG-TS', file=sys.stderr)
                kind = 'mpegts'
                playlist = make_hls(work, cut, kind)
                failures = seek_failures(work, playlist, cut)
                if failures:
                    raise ValueError('MPEG-TS input seek: ' + '; '.join(failures))
            init, segments = playlist_parts(playlist)
            for name in ([init] if init else []) + [uri for uri, _ in segments]:
                shutil.copyfile(playlist.parent/name, out/name)
            result = {'strategy': 'boundary-only', 'format': kind, 'streamCopiedFrames': copied,
                      'reencodedFrames': encoded, 'audio': 'entire kept track encoded once as AAC-LC',
                      'fallbackReason': fallback, 'packagingSeconds': round(time.monotonic() - started, 3)}
            (out/'packaging.json').write_text(json.dumps(result, indent=2) + '\n')
            shutil.copyfile(playlist, out/'.main.m3u8.tmp')
        # The playlist is the last artifact published. An earlier failure
        # cannot leave a playable endpoint or fall back to unfiltered media.
        (out/'.main.m3u8.tmp').replace(out/'main.m3u8')
    except BaseException:
        shutil.rmtree(out)
        raise
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'plan', 'out'):
        parser.add_argument(f'--{name}', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = package(args.source.resolve(), args.plan.resolve(), args.out.absolute())
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'FAIL: {exc}\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
