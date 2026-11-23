#!/usr/bin/env python3
"""Make HDMI-shaped capture files for the TV harness's device-mode dry run.

These stand in for what a USB capture adapter would record from an ideal,
exact HDMI path: 1920x1080 at 60 Hz (each 25 fps frame repeated on a 60 Hz
cadence), one second of black before and after, and 48 kHz stereo 16-bit PCM
with a constant delay. They test the device code path and the scoring, never
a device. From the repository root:

    python3 tools/playback-lab/hdmi-sim.py NEW_DIRECTORY

Writes control.mkv (uncut), filtered.mkv (prefix + suffix of the hard fixture)
and lossless.mkv (the identifiable-sample FLAC fixture, decoded).
"""
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from hard_fixture_lab import FPS, HZ, media, write_identifiable_movie  # noqa: E402
from exact_audio import plan_frames  # noqa: E402

LEAD_S = 1
DELAY_SAMPLES = HZ * LEAD_S + 137  # constant, deliberately not frame-aligned


def decoded(work, name):
    return media(work, 'ffmpeg', '-i', f'/lab/{name}', '-map', '0:a:0', '-f', 's16le',
                 '-ac', '2', '-ar', str(HZ), 'pipe:1', readonly=True)


def render(work, source, pcm, out, select=None):
    raw = work/(out + '.raw')
    silence = b'\0\0\0\0'
    raw.write_bytes(silence*DELAY_SAMPLES + pcm + silence*HZ*LEAD_S)
    chain = (f"select='not(between(n,{select[0]},{select[1]-1}))',setpts=N/({FPS}*TB)," if select else '')
    chain += (f'tpad=start_duration={LEAD_S}:stop_duration={LEAD_S}:color=black,'
              'fps=60,scale=1920:1080:flags=bicubic,format=yuv420p')
    try:
        media(work, 'ffmpeg', '-i', f'/lab/{source}', '-f', 's16le', '-ar', str(HZ), '-ac', '2',
              '-i', f'/lab/{raw.name}', '-map', '0:v:0', '-map', '1:a:0', '-vf', chain,
              '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16', '-c:a', 'pcm_s16le',
              f'/lab/{out}.mkv')
    finally:
        raw.unlink()


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    work = Path(sys.argv[1]).resolve()
    work.mkdir()
    fixture = work/'fixture'
    subprocess.run([sys.executable, HERE/'hard-fixture.py', fixture], check=True, stdout=subprocess.DEVNULL)
    plan = json.loads((fixture/'plan.json').read_text())
    start, end, total = plan_frames(plan)
    cut, = plan['cuts']
    pcm = decoded(fixture, 'source.mp4')[:total*4]
    render(fixture, 'source.mp4', pcm, 'control')
    render(fixture, 'source.mp4', pcm[:start*4] + pcm[end*4:], 'filtered', (cut['startFrame'], cut['endFrame']))
    write_identifiable_movie(fixture, 20*HZ)
    render(fixture, 'identifiable.mp4', decoded(fixture, 'identifiable.mp4'), 'lossless')
    for name in ('control', 'filtered', 'lossless'):
        (fixture/(name+'.mkv')).rename(work/(name+'.mkv'))
    print(json.dumps({k: str(work/(k+'.mkv')) for k in ('control', 'filtered', 'lossless')}))


if __name__ == '__main__':
    main()
