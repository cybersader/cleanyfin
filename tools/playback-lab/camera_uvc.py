"""Read-only UVC discovery and injected setting policy for an approved camera backend."""
from contextlib import contextmanager
from pathlib import Path
import re
import subprocess
from typing import Protocol

from camera_picture import CalibrationError
from tv_device import ADAPTERS, FFMPEG_PIXFMT, _read, _usb_device, parse_v4l2_ctl

MODE_REASON = ('Highest explicitly listed fps at 1280x720 or larger, among supported formats; '
               'tie: more pixels, then uncompressed, then format/node order. Verify actual cadence from timestamps.')
PTZ = ('pan_absolute', 'tilt_absolute', 'zoom_absolute')
LOCKS = (
    ('exposure', ('auto_exposure', 'exposure_auto'), 1, ('exposure_time_absolute', 'exposure_absolute')),
    ('focus', ('focus_automatic_continuous', 'focus_auto'), 0, ('focus_absolute',)),
    ('whiteBalance', ('white_balance_automatic', 'white_balance_temperature_auto'), 0, ('white_balance_temperature',)),
)
RATE_CONTROLS = ('exposure_dynamic_framerate', 'exposure_auto_priority')


def _discover(root):
    by_usb = {}
    for node in sorted((root/'sys/class/video4linux').glob('video*')):
        device = (node/'device').resolve()
        usb = _usb_device(device)
        if usb is None or not any((p/'driver').is_symlink() and (p/'driver').resolve().name == 'uvcvideo'
                                  for p in (device, *device.parents) if p.is_relative_to(usb)):
            continue
        key = f"{_read(usb/'idVendor')}:{_read(usb/'idProduct')}".lower()
        item = by_usb.setdefault(usb, dict(usb=key, product=_read(usb/'product'), video=[], microphones=[]))
        item['video'].append(dict(node=f'/dev/{node.name}', name=_read(node/'name')))
    for card in sorted((root/'sys/class/sound').glob('card*')):
        usb = _usb_device((card/'device').resolve())
        if usb not in by_usb or not card.name[4:].isdigit():
            continue
        n = int(card.name[4:])
        proc = root/f'proc/asound/card{n}'
        ident = _read(proc/'id')
        if not re.fullmatch(r'[A-Za-z0-9_]+', ident):
            continue
        for endpoint in sorted(proc.glob('pcm*c')):
            number = endpoint.name[3:-1]
            if number.isdigit():
                by_usb[usb]['microphones'].append(dict(card=n, cardId=ident, device=int(number),
                                                       alsa=f'plughw:CARD={ident},DEV={number}'))
    return list(by_usb.values())


def _query(command):
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=5)


def preflight(selector=None, *, root=Path('/'), query=_query):
    """Enumerate USB UVC devices and read mode lists; no STREAMON and no writes."""
    inventory = _discover(Path(root))
    for camera in inventory:
        modes = []
        for video in camera['video']:
            try:
                video['modes'] = parse_v4l2_ctl(query(['v4l2-ctl', '-d', video['node'], '--list-formats-ext']))
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                video.update(modes=[], queryError=str(exc))
            for mode in video['modes']:
                if (mode['width'] >= 1280 and mode['height'] >= 720 and mode['fourcc'] in FFMPEG_PIXFMT):
                    modes.extend(dict(mode, fps=fps, node=video['node'], inputFormat=FFMPEG_PIXFMT[mode['fourcc']])
                                 for fps in mode['fps'] if fps > 0)
        modes.sort(key=lambda m: (-m['fps'], -m['width']*m['height'], m['compressed'], m['fourcc'], m['node']))
        words = ' '.join([camera['product'], *(v['name'] for v in camera['video'])]).casefold()
        camera['matchesSelection'] = (selector is None or
                                      (selector.casefold() == camera['usb'] if ':' in selector else
                                       selector.casefold() in words))
        camera['chosenMode'] = modes[0] if modes and camera['usb'] not in ADAPTERS and 'hdmi' not in words else None
        camera['modeReason'] = MODE_REASON if camera['chosenMode'] else (
            'Known or advertised HDMI input; not a webcam' if camera['usb'] in ADAPTERS or 'hdmi' in words else
            'No supported, explicitly rated >=720p mode found; install v4l2-ctl or check permissions/device modes')
    matching = [c for c in inventory if c['matchesSelection'] and (selector is not None or c['chosenMode'])]
    selected = matching[0] if len(matching) == 1 and matching[0]['chosenMode'] else None
    return dict(evidence='screen camera witness', cameras=inventory, selected=selected,
                status='ready' if selected else 'No unique usable webcam; use --camera VID:PID or a unique name')


def parse_controls(text):
    found = {}
    for line in text.splitlines():
        header = re.match(r'^\s*(\w+)\s+0x[0-9a-fA-F]+\s+\(([^)]+)\)\s*:\s*(.*)$', line)
        if not header:
            continue
        name, kind, tail = header.groups()
        values = {k: int(v) for k, v in re.findall(r'\b(min|max|step|value)=(-?\d+)', tail)}
        if 'value' in values and kind in ('int', 'bool', 'menu') and not any(
                flag in tail for flag in ('read-only', 'write-only', 'disabled')):
            found[name] = values
    return found


class ControlBackend(Protocol):
    """Accepted calls must stay inside the service's owner-approved session."""

    def read(self) -> dict[str, dict[str, int]]:
        """Return current values and any standard integer ranges and steps."""
        ...

    def set(self, key: str, value: int) -> None:
        """Set an allowed standard control; raise if refused or unavailable."""
        ...

_CONTROL_ERRORS = (OSError, ValueError, KeyError, subprocess.CalledProcessError, subprocess.TimeoutExpired)


class Settings:
    """Readback-verified control transactions; never mutate without an original."""
    def __init__(self, backend: ControlBackend, report: dict):
        self.backend = backend
        self.report = report
        self.restore_order = []
        try:
            self.initial = backend.read()
        except _CONTROL_ERRORS as exc:
            self.initial = {}
            report['controlReadError'] = str(exc)
        report['originalControls'] = {key: v['value'] for key, v in self.initial.items()
                                      if key in (*PTZ, *RATE_CONTROLS, *(k for _, a, _, b in LOCKS for k in (*a, *b)))}
        report['standardAimingControls'] = {key: self.initial[key] for key in PTZ if key in self.initial}

    def set(self, key, value):
        if key not in self.initial:
            raise ValueError(f'No restorable standard control: {key}')
        if self.backend.read()[key]['value'] != value:
            if key not in self.restore_order:
                self.restore_order.append(key)
            self.backend.set(key, value)
        if self.backend.read()[key]['value'] != value:
            raise ValueError(f'Camera did not retain {key}={value}')
        return {key: value}

    def lock(self):
        for key in RATE_CONTROLS:
            if key in self.initial:
                self._setting(key, lambda: self.set(key, 0))
        for label, autos, manual, values in LOCKS:
            auto = next((k for k in autos if k in self.initial), None)
            value = next((k for k in values if k in self.initial), None)
            if auto is None or value is None:
                self.report.setdefault('locks', {})[label] = dict(locked=False, reason='Standard controls unavailable')
                continue
            def freeze():
                # Manual values may change automatically when the auto mode is disabled.
                # Register them for restore even when the driver reports no explicit change.
                if value not in self.restore_order:
                    self.restore_order.append(value)
                self.set(auto, manual)
                self.set(value, self.initial[value]['value'])
                return {auto: manual, value: self.initial[value]['value']}
            self._setting(label, freeze)

    def _setting(self, label, callback):
        try:
            outcome = dict(locked=True, readback=callback())
        except _CONTROL_ERRORS as exc:
            outcome = dict(locked=False, reason=str(exc))
        self.report.setdefault('locks', {})[label] = outcome

    def restore(self):
        # Restore absolute controls while the manual switches still hold, then
        # return the automatic controls to their previous mode.
        switches = {k for _, keys, _, _ in LOCKS for k in keys}
        ordered = list(reversed(self.restore_order))
        ordered.sort(key=lambda key: 2 if key in RATE_CONTROLS else 1 if key in switches else 0)
        results = {}
        for key in ordered:
            try:
                expected = self.initial[key]['value']
                actual = self.backend.read()[key]['value']
                if actual != expected:
                    self.backend.set(key, expected)
                    actual = self.backend.read()[key]['value']
                results[key] = dict(expected=expected, actual=actual, restored=actual == expected)
            except _CONTROL_ERRORS as exc:
                results[key] = dict(restored=False, reason=str(exc))
        self.report['restore'] = results


@contextmanager
def locked_settings(backend, report):
    settings = Settings(backend, report)
    try:
        settings.lock()
        yield settings
    finally:
        settings.restore()


def _positions(initial):
    axes = {k: v for k, v in initial.items() if k in PTZ and {'min', 'max', 'step', 'value'} <= v.keys()}
    base = {k: v['value'] for k, v in axes.items()}
    yield base
    zoom = 'zoom_absolute'
    wide = dict(base, zoom_absolute=axes[zoom]['min']) if zoom in axes else base
    yield wide
    # Small nudges around the starting aim (not an unbounded gimbal scan).
    # Keep a wider shot while addressing clipping; zoom a single nudge to try
    # to rescue cells too small to read, always validating all four corners.
    for key in PTZ:
        if key not in axes:
            continue
        limits = axes[key]
        step = max(1, limits['step'])
        lo, hi = limits['min'], limits['max']
        nudge = max(1, (hi-lo)//(12*step))*step
        for sign in (1, -1):
            origin = wide if key != zoom else base
            val = min(hi, max(lo, origin[key]+sign*nudge))
            val = lo+(val-lo)//step*step
            yield dict(origin, **{key: val})


def aim_camera(settings, try_calibration, *, enabled):
    """Try at most eight bounded UVC positions using calibration as the only oracle."""
    attempts, seen = [], set()
    settings.report['aiming'] = dict(requested=enabled, attempts=attempts)
    initial = {k: v for k, v in settings.initial.items() if k in PTZ}
    positions = _positions(initial) if enabled else ({},)
    if enabled and not initial:
        settings.report['aiming']['note'] = 'No standard UVC pan, tilt or zoom controls; aim manually'
    for position in positions:
        encoded = tuple(sorted(position.items()))
        if encoded in seen:
            continue
        seen.add(encoded)
        trial = dict(requested=position)
        attempts.append(trial)
        try:
            for key, value in position.items():
                settings.set(key, value)
        except _CONTROL_ERRORS as exc:
            trial['reason'] = str(exc)
            continue
        try:
            calibration = try_calibration()
        except CalibrationError as exc:
            trial['reason'] = str(exc)
            continue
        actual = settings.backend.read() if initial else {}
        settings.report['aiming']['final'] = {k: actual[k]['value'] for k in initial if k in actual}
        trial['calibrated'] = True
        return calibration
    raise CalibrationError(f"No valid screen framing after {len(attempts)} attempt(s): {attempts[-1].get('reason')}")


def select_microphone(camera, desktop_default_card):
    """Select one same-USB ALSA endpoint; never use a default or alternate mic."""
    mics = [m for m in camera['microphones'] if m['card'] != desktop_default_card]
    if len(mics) != 1:
        raise ValueError('Need one explicit same-USB webcam mic; make a different mic the desktop default '
                         'if this camera is the default. No default-mic fallback.')
    return mics[0]
