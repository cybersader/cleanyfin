"""One manually chosen navigation step on an already connected wireless TV.

Run with --help. The caller owns connect/disconnect and must inspect the focus
before selecting anything. Temporary comparison images are private, not logs.
"""
import argparse
import ipaddress
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import zlib
from typing import Protocol

SIZE = (1920, 1080)
KEYS = {'HOME', 'BACK', 'DPAD_UP', 'DPAD_DOWN', 'DPAD_LEFT', 'DPAD_RIGHT', 'DPAD_CENTER'}
DEFAULT_DIR = Path.home() / '.cache/cleanyfin-scratch/drive-session'


def endpoint(text):
    try:
        host, port = text.rsplit(':', 1)
        ipaddress.IPv4Address(host)
        if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535 or int(port) == 5555:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError('use an explicit paired IPv4:TLS-port (not legacy ADB)') from None
    return text


def crop(text):
    try:
        x, y, w, h = map(int, text.split(','))
        if min(x, y) < 0 or min(w, h) <= 0 or x+w > SIZE[0] or y+h > SIZE[1]:
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError('expected TV-only X,Y,W,H inside 1920x1080') from None
    return x, y, w, h


def capture(command):
    return subprocess.run(command, check=True, capture_output=True, timeout=10).stdout


class Camera(Protocol):
    def grab(self) -> bytes:
        """Return one TV-only PNG. Release the backend before returning."""
        ...


class Link2:
    def __init__(self, node, roi):
        self.node, self.roi = node, roi

    def grab(self):
        x, y, w, h = self.roi
        return capture(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'v4l2', '-input_format',
                        'mjpeg', '-video_size', '1920x1080', '-probesize', '32',
                        '-analyzeduration', '0', '-i', self.node, '-an', '-frames:v', '1',
                        '-vf', f'crop={w}:{h}:{x}:{y}:exact=1', '-c:v', 'png',
                        '-f', 'image2pipe', 'pipe:1'])


class WirelessTV:
    def __init__(self, target):
        self.adb = ['adb', '-s', endpoint(target)]

    def send(self, action):
        if action == 'SETTINGS':
            capture(self.adb + ['shell', 'am', 'start', '-a', 'android.settings.SETTINGS'])
        elif action in KEYS:
            capture(self.adb + ['shell', 'input', 'keyevent', 'KEYCODE_' + action])
        else:
            raise ValueError('unsupported action')

    def screen(self):
        return capture(self.adb + ['exec-out', 'screencap', '-p'])


class DryRun:
    def send(self, action):
        pass

    def grab(self):
        # Valid synthetic black pixel; no optional dependencies or device I/O.
        def chunk(name, data):
            return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))
        return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 0, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(b'\x00\x00')) + chunk(b'IEND', b''))

    screen = grab


def step(action, tv, camera: Camera, out, settle, *, clock=time.monotonic, wait=time.sleep):
    if action not in KEYS | {'SETTINGS'} or not math.isfinite(settle) or not 0 <= settle <= 10:
        raise ValueError('invalid navigation step or settle time')
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='look-', dir=out))
    try:
        start = clock()
        tv.send(action)
        sent = clock()
        wait(settle)
        opened = clock()
        cam = camera.grab()
        seen = clock()
        screen = tv.screen()
        done = clock()
        paths = {}
        for name, data in [('webcam', cam), ('screencap', screen)]:
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError(f'{name}: expected PNG')
            path = directory / (name + '.png')
            path.write_bytes(data)
            paths[name] = str(path)
        return {**paths, 'timing_ms': {k: round(v*1000, 1) for k, v in {
            'action_round_trip': sent-start, 'camera_call': seen-opened,
            'action_to_webcam_return': seen-start, 'action_to_pair_return': done-start,
        }.items()}}
    except BaseException:
        shutil.rmtree(directory)
        raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=sorted(KEYS | {'SETTINGS'}))
    p.add_argument('--target', type=endpoint, help='already connected paired IPv4:TLS-port')
    p.add_argument('--camera', default='/dev/video3')
    p.add_argument('--crop', type=crop, help='required live: TV-only X,Y,W,H, checked by operator')
    p.add_argument('--out', type=Path, default=DEFAULT_DIR)
    p.add_argument('--settle', type=float, default=0.45, help='seconds after action returns')
    p.add_argument('--dry-run', action='store_true', help='only synthetic pixels and timing')
    args = p.parse_args(argv)
    if args.dry_run:
        tv = camera = DryRun()
    else:
        if not args.target or not args.crop:
            p.error('live mode needs --target and a verified TV-only --crop')
        tv, camera = WirelessTV(args.target), Link2(args.camera, args.crop)
    print(json.dumps({'action': args.action, 'dry_run': args.dry_run,
                      **step(args.action, tv, camera, args.out, args.settle)}))


if __name__ == '__main__':
    main()
