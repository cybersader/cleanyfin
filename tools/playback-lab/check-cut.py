#!/usr/bin/env python3
"""Offline reference test: remove [2, 4) seconds; retain all other picture and sound."""
import os
import subprocess
import tempfile
from pathlib import Path

from hard_fixture_lab import IMAGE  # one pinned media image; see JELLYFIN there
FPS, HZ, SIDE = 25, 48000, 64


def run_lab(work):
    def ff(*args):
        return subprocess.check_output([
            'docker', 'run', '--rm', '--network', 'none', '--user', f'{os.getuid()}:{os.getgid()}',
            '-v', f'{work}:/lab:Z', '-w', '/lab', '--entrypoint', '/usr/lib/jellyfin-ffmpeg/ffmpeg',
            IMAGE, '-hide_banner', '-loglevel', 'error', '-nostdin', *args])

    # Lossless on purpose: distinguish a cut bug from lossy codec artifacts.
    ff('-f', 'lavfi', '-i', "color=c=blue:s=64x64:r=25:d=6,drawbox=color=red:t=fill:enable='gte(t,2)*lt(t,4)',drawbox=color=lime:t=fill:enable='gte(t,4)'",
       '-f', 'lavfi', '-i', r'aevalsrc=0.25*sin(2*PI*t*if(lt(t\,2)\,440\,if(lt(t\,4)\,880\,660))):s=48000:d=6',
       '-c:v', 'ffv1', '-c:a', 'pcm_s16le', 'source.mkv')
    ff('-i', 'source.mkv', '-filter_complex',
       '[0:v]trim=end=2,setpts=PTS-STARTPTS[v0];[0:a]atrim=end=2,asetpts=PTS-STARTPTS[a0];'
       '[0:v]trim=start=4,setpts=PTS-STARTPTS[v1];[0:a]atrim=start=4,asetpts=PTS-STARTPTS[a1];'
       '[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]',
       '-map', '[v]', '-map', '[a]', '-c:v', 'ffv1', '-c:a', 'pcm_s16le', 'cut.mkv')
    for label, units_per_s, unit_size, decoder in (
            ('video frames', FPS, SIDE*SIDE*3, ['-map', '0:v:0', '-pix_fmt', 'rgb24', '-f', 'rawvideo']),
            ('mono audio samples', HZ, 2, ['-map', '0:a:0', '-c:a', 'pcm_s16le', '-f', 's16le'])):
        source, actual = [ff('-i', f, *decoder, 'pipe:1') for f in ('source.mkv', 'cut.mkv')]
        second = units_per_s * unit_size
        if len(source) != 6 * second or len({source[i*second:(i+2)*second] for i in (0, 2, 4)}) != 3:
            raise SystemExit(f'FAIL: {label} fixture must have three distinct two-second sections')
        expected = source[:2*second] + source[4*second:]
        if actual != expected:
            raise SystemExit(f'FAIL: {label} differ from the original outside the requested cut')
        print(f'PASS: {4*units_per_s:,} {label} kept, {2*units_per_s:,} removed', flush=True)


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='cleanyfin-cut-') as directory:
        run_lab(Path(directory))
