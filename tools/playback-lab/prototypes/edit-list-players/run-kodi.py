#!/usr/bin/env python3
"""Run disposable Kodi with an Xvfb screen and record its PulseAudio sink."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parent
LAB = HERE.parent.parent
MEDIA_IMAGE = 'jellyfin/jellyfin@sha256:aefb67e6a7ff1debdd154a78a7bbb780fd0c873d8639210a7f6a2016ad2b35db'
CAPTURE_FPS = 50


def halt(proc, sig=signal.SIGTERM):
    if proc.poll() is None:
        proc.send_signal(sig)
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def wait_for(proc, ready, seconds, description):
    limit = time.monotonic() + seconds
    while time.monotonic() < limit:
        if proc.poll() is not None:
            raise RuntimeError(f'{description}: exited {proc.returncode}')
        if ready():
            return
        time.sleep(0.05)
    raise RuntimeError(f'{description}: timed out after {seconds} s')


def in_container(label):
    work = Path('/lab')
    with tempfile.TemporaryDirectory(prefix='kodi-lab-') as base, ExitStack() as stack:
        base = Path(base)
        env = os.environ.copy()
        env.update(HOME=str(base), XDG_RUNTIME_DIR=str(base), DISPLAY=':88', LIBGL_ALWAYS_SOFTWARE='1',
                   PULSE_SERVER=f'unix:{base}/pulse.sock', PULSE_SINK='lab', PULSE_SOURCE='lab.monitor')

        def start(log, *args, stop_signal=signal.SIGTERM):
            sink = stack.enter_context((work / log).open('w'))
            proc = subprocess.Popen(args, env=env, stdout=sink, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            stack.callback(halt, proc, stop_signal)
            return proc

        xvfb = start('xvfb.log', 'Xvfb', ':88', '-screen', '0', '1280x720x24', '-nolisten', 'tcp', '-noreset')
        wait_for(xvfb, lambda: Path('/tmp/.X11-unix/X88').is_socket(), 5, 'Xvfb')
        pa = start('pulse.log', 'pulseaudio', '-n', '--daemonize=no', '--exit-idle-time=-1',
                   '--use-pid-file=no', '--log-target=stderr',
                   f'--load=module-native-protocol-unix socket={base}/pulse.sock',
                   '--load=module-null-sink sink_name=lab rate=48000 channels=2')
        wait_for(pa, lambda: subprocess.run(['pactl', 'list', 'short', 'sources'], env=env, timeout=2,
                                           capture_output=True).stdout.find(b'lab.monitor') >= 0,
                 5, 'PulseAudio null sink')
        progress = work / 'capture.progress'
        capture = start('ffmpeg.log', 'ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin', '-y',
                        '-stats_period', '0.25', '-progress', str(progress),
                        '-thread_queue_size', '32', '-f', 'x11grab', '-draw_mouse', '0', '-video_size', '1280x720',
                        '-framerate', str(CAPTURE_FPS), '-i', ':88', '-thread_queue_size', '64',
                        '-f', 'pulse', '-sample_rate', '48000', '-channels', '2', '-i', 'lab.monitor',
                        '-map', '0:v:0', '-map', '1:a:0', '-fps_mode', 'passthrough',
                        '-c:v', 'ffv1', '-threads:v', '4', '-c:a', 'pcm_s16le', '-t', '26', '/lab/capture.mkv',
                        stop_signal=signal.SIGINT)
        wait_for(capture, lambda: progress.exists() and 'frame=' in progress.read_text(), 8, 'FFmpeg capture')
        kodi_log = base / '.kodi' / 'temp' / 'kodi.log'
        kodi = start('kodi-console.log', 'kodi', '--windowing=x11', '--fullscreen', '--nosplash', '/lab/source.mp4')
        try:
            # CLI launch autoplays the local fixture. Preserve raw pre/post-roll in the capture.
            def playback_ended():
                text = kodi_log.read_text(errors='replace') if kodi_log.exists() else ''
                return 'OnPlayBackEnded' in text or 'CVideoPlayer::OnExit()' in text

            wait_for(kodi, playback_ended, 25, 'Kodi playback completion')
            time.sleep(0.3)
        finally:
            halt(capture, signal.SIGINT)
            if kodi_log.exists():
                shutil.copyfile(kodi_log, work / 'kodi.log')
        if not (work / 'capture.mkv').stat().st_size:
            raise RuntimeError(f'{label}: empty screen/audio capture')


def run_container(image, work, label):
    cidfile = work.parent / 'container.cid'
    command = ['docker', 'run', '--rm', '--pull=never', '--network', 'none', '-i',
               '--user', f'{os.getuid()}:{os.getgid()}', '--shm-size=128m', '--cidfile', str(cidfile),
               '-v', f'{work}:/lab:Z', '-w', '/lab', '--entrypoint', 'python3', image, '-I', '-', '--inside', label]
    try:
        return subprocess.run(command, input=Path(__file__).read_text(), text=True, timeout=48)
    finally:
        if cidfile.exists():
            subprocess.run(['docker', 'rm', '-f', cidfile.read_text().strip()], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cidfile.unlink()


def lab_script(name, *args, **kwargs):
    return subprocess.run([sys.executable, str(LAB / name), *map(str, args)], **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='cleanyfin-edl-lab:kodi')
    args = parser.parse_args()
    details = json.loads(subprocess.check_output(['docker', 'image', 'inspect', args.image], text=True))[0]
    image = details['Id']
    print(f"Image {image} ({details['Size']} bytes), screen capture {CAPTURE_FPS} fps", flush=True)
    subprocess.run(['docker', 'run', '--rm', '--pull=never', '--network', 'none', '--entrypoint', 'kodi',
                    image, '--version'], check=True)
    subprocess.run(['docker', 'image', 'inspect', MEDIA_IMAGE], stdout=subprocess.DEVNULL, check=True)
    with tempfile.TemporaryDirectory(prefix='cleanyfin-kodi-') as tmp:
        work = Path(tmp)
        fixture = work / 'fixture'
        lab_script('hard-fixture.py', fixture, check=True)
        for label in ('control', 'cut'):
            dest = work / label
            dest.mkdir()
            shutil.copyfile(fixture / 'source.mp4', dest / 'source.mp4')
            if label == 'cut':
                shutil.copyfile(fixture / 'fixture.edl', dest / 'source.edl')
            result = run_container(image, dest, label)
            print(f'\n{label} (Kodi), container exit {result.returncode}', flush=True)
            for name in ('xvfb.log', 'pulse.log', 'ffmpeg.log', 'kodi-console.log', 'kodi.log'):
                path = dest / name
                if path.exists():
                    lines = path.read_text(errors='replace').splitlines()
                    if result.returncode or name == 'ffmpeg.log':
                        relevant = lines[-25:]
                    elif name == 'kodi.log':
                        relevant = [line for line in lines if any(key in line.lower() for key in (
                            'edit decision', 'edl', 'pulseaudio: opened', 'openfile: /lab/source.mp4',
                            'gl_renderer', 'skip scene', 'ffmpeg version'))]
                    else:
                        relevant = []
                    if relevant:
                        print(f'{name}:\n' + '\n'.join(relevant), flush=True)
            if result.returncode:
                raise SystemExit(result.returncode)
            recording = dest / 'capture.mkv'
            measurement = lab_script('analyze-capture.py', '--plan', fixture / 'plan.json', '--video', recording,
                                     '--audio', recording, '--json', capture_output=True, text=True, timeout=90)
            print(measurement.stdout, end='', flush=True)
            if measurement.stderr:
                print(measurement.stderr, end='', file=sys.stderr)
            metrics = json.loads(measurement.stdout.splitlines()[-1])
            if 'error' in metrics:
                raise RuntimeError('scorer failed to analyze Kodi capture')
            v, a = metrics['video'], metrics['audio']
            print(f'Approximate recorded rate: {v["frames"] * 1000 / a["captureMs"]:.1f} frames/s '
                  '(video frame count / audio capture duration)', flush=True)
            if label == 'control' and (not v['markedCount'] or abs(a['leakMs'] - 5040) > 10):
                raise RuntimeError('Control did not detect both marked picture and the near-full marked tone')
        print(f'Temporary on-disk artifacts: {sum(p.stat().st_blocks*512 for p in work.rglob("*") if p.is_file())} bytes', flush=True)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--inside']:
        in_container(sys.argv[2])
    else:
        main()
