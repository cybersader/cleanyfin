"""Real-box helpers for the TV harness: identity, change manifest, HDMI capture adapter.

Parsing, diffing and restore planning are pure and unit-tested. Nothing here
scans a network: the box is reached only at the address the owner types in.
"""
import ipaddress
import os
import re
from pathlib import Path

APP = 'com.github.damontecres.wholphin'
WHOLPHIN_VERSION = '1.1.2'

# Official Wholphin v1.1.2 release assets, by Android ABI. Unmodified builds only.
ASSETS = {
    'x86_64': ('Wholphin-x86_64.apk', '60305215500d033ffd55333adc0066a8ab15dc146f2262735cbb6e7e60ce692d',
               53836582, 'lib/x86_64/libmpv.so'),
    'arm64-v8a': ('Wholphin-arm64-v8a.apk', '5c1cec6cae94614fb7e561017bfb73adc8c621390ea31492893bb6307cff7144',
                  46709701, 'lib/arm64-v8a/libmpv.so'),
}

# USB HDMI capture adapters by vendor:product. MS2130 is the USB 3.0 class the
# run-sheet asks for; MS2109 is the older USB 2.0 part (1080p60 only as MJPEG).
# An adapter not listed here can be named with --capture-usb VID:PID.
ADAPTERS = {
    '345f:2130': 'MacroSilicon MS2130 (USB 3.0)',
    '534d:2130': 'MacroSilicon MS2130 (USB 3.0)',
    '534d:2109': 'MacroSilicon MS2109 (USB 2.0)',
}
WEBCAM_WORDS = ('camera', 'webcam', 'cam ', 'facetime', 'brio', 'c920', 'c922', 'integrated')

# Settings the harness may change for a run, and must put back.
TOUCHED = (
    ('global', 'encoded_surround_output'),           # HDMI audio: 1 = never bitstream (PCM)
    ('secure', 'match_content_frame_rate'),          # 0 never, 1 seamless, 2 always
)
# Read-only context recorded beside the touched settings.
CONTEXT = (
    ('global', 'encoded_surround_output_enabled_formats'),
    ('global', 'user_preferred_resolution_width'),
    ('global', 'user_preferred_resolution_height'),
    ('global', 'user_preferred_refresh_rate'),
    ('global', 'adb_wifi_enabled'),
)
MUSIC_STREAM = 3


# ---------------------------------------------------------------- connection

def lan_address(text, label):
    """Validate an owner-typed IPv4:port on the local network. Never echo it back."""
    m = re.fullmatch(r'\s*(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})\s*', text or '')
    if not m:
        raise ValueError(f'{label} must look like 192.168.x.x:port (shown on the TV screen)')
    ip, port = ipaddress.IPv4Address(m[1]), int(m[2])
    if not (ip.is_private or ip.is_link_local) or ip.is_loopback or not 0 < port < 65536:
        raise ValueError(f'{label} is not a home-network address')
    return f'{ip}:{port}'


def connection(env, ask, ask_secret):
    """Pairing address, pairing code and debugging address: from env for this run, else prompted."""
    pair = env.get('CLEANYFIN_TV_PAIR') or ask('Pairing IP address & port (from "Pair device with pairing code"): ')
    code = env.get('CLEANYFIN_TV_CODE') or ask_secret('Six-digit Wi-Fi pairing code: ')
    connect = env.get('CLEANYFIN_TV_CONNECT') or ask('IP address & port shown at the top of "Wireless debugging": ')
    pair, connect = lan_address(pair, 'Pairing address'), lan_address(connect, 'Debugging address')
    if pair.split(':')[0] != connect.split(':')[0]:
        raise ValueError('Pairing and debugging addresses are on different devices')
    code = (code or '').strip()
    if not re.fullmatch(r'\d{6}', code):
        raise ValueError('Pairing code must be the six digits shown on the TV')
    return pair, code, connect


# ------------------------------------------------------------------ identity

def parse_getprop(text):
    return {m[1]: m[2] for m in re.finditer(r'(?m)^\[([^\]]+)\]: \[(.*)\]\s*$', text)}


def parse_packages(text):
    """`pm list packages --show-versioncode` -> {name: versionCode}."""
    out = {}
    for line in text.splitlines():
        m = re.fullmatch(r'\s*package:([\w.]+)(?:\s+versionCode:(-?\d+))?\s*', line)
        if m:
            out[m[1]] = m[2]
    return out


def is_emulator(props):
    return (props.get('ro.kernel.qemu') == '1' or props.get('ro.boot.qemu') == '1'
            or props.get('ro.hardware') in ('ranchu', 'goldfish'))


def identity(props, features, packages, *, emulator=False, expect_model=None):
    """Return (accepted, found, reason). Accept only the owner's Google TV box (or, in a
    dry run, the harness's own emulator). Prints nothing; caller shows `found`."""
    found = {k: props.get('ro.product.' + k, '') for k in ('manufacturer', 'brand', 'model', 'device')}
    found.update(android=props.get('ro.build.version.release', ''), sdk=props.get('ro.build.version.sdk', ''),
                 build=props.get('ro.build.fingerprint', ''), abi=props.get('ro.product.cpu.abi', ''),
                 characteristics=props.get('ro.build.characteristics', ''),
                 leanback='feature:android.software.leanback' in features,
                 googleTvLauncher='com.google.android.apps.tv.launcherx' in packages,
                 emulator=is_emulator(props))
    if not found['leanback']:
        return False, found, 'not a TV device (no leanback feature)'
    if not found['googleTvLauncher']:
        return False, found, 'not Google TV (no Google TV home launcher)'
    if found['abi'] not in ASSETS:
        return False, found, f"no pinned Wholphin build for ABI {found['abi'] or 'unknown'}"
    if emulator:
        return (True, found, 'harness-owned emulator (dry run)') if found['emulator'] else \
            (False, found, 'dry run expected the harness emulator but found a real device')
    if found['emulator']:
        return False, found, 'emulator, not the borrowed box'
    names = ' '.join(found[k] for k in ('manufacturer', 'brand', 'model')).casefold()
    if not re.search(r'(?<![a-z])onn(?![a-z])', names):
        return False, found, 'not an onn. device'
    if expect_model and found['model'] != expect_model:
        return False, found, 'model differs from --expect-model'
    return True, found, 'onn. Google TV box'


# ------------------------------------------------------------------ manifest

def parse_volume(text):
    m = re.search(r'volume is (\d+) in range \[(\d+)\.\.(\d+)\]', text, re.I)
    return dict(value=int(m[1]), min=int(m[2]), max=int(m[3])) if m else None


def parse_version_name(dumpsys):
    m = re.search(r'versionName=(\S+)', dumpsys)
    return m[1] if m else None


def setting_value(raw):
    v = raw.strip()
    return None if v in ('', 'null') else v


def manifest(adb):
    """Snapshot names/versions and the settings the run may touch. `adb(*args) -> str`."""
    packages = parse_packages(adb('shell', 'pm', 'list', 'packages', '--show-versioncode'))
    settings = {f'{ns}/{key}': setting_value(adb('shell', 'settings', 'get', ns, key)) for ns, key in TOUCHED + CONTEXT}
    volume = parse_volume(adb('shell', 'cmd', 'media_session', 'volume', '--stream', str(MUSIC_STREAM), '--get'))
    wholphin = parse_version_name(adb('shell', 'dumpsys', 'package', APP)) if APP in packages else None
    scratch = sorted(x for x in adb('shell', 'ls', '/data/local/tmp').split() if x.startswith(('lab-', 'cleanyfin')))
    return dict(packages=packages, settings=settings, musicVolume=volume, wholphinVersion=wholphin, labScratch=scratch)


def diff(before, after):
    """Human-readable differences between two manifests (empty list = identical)."""
    out = []
    a, b = before['packages'], after['packages']
    out += [f'+ package {p} {b[p]}' for p in sorted(set(b) - set(a))]
    out += [f'- package {p} {a[p]}' for p in sorted(set(a) - set(b))]
    out += [f'~ package {p} {a[p]} -> {b[p]}' for p in sorted(set(a) & set(b)) if a[p] != b[p]]
    for k in sorted(set(before['settings']) | set(after['settings'])):
        x, y = before['settings'].get(k), after['settings'].get(k)
        if x != y:
            out.append(f'~ setting {k}: {x} -> {y}')
    vx, vy = (before.get('musicVolume') or {}).get('value'), (after.get('musicVolume') or {}).get('value')
    if vx != vy:
        out.append(f'~ media volume: {vx} -> {vy}')
    if before.get('wholphinVersion') != after.get('wholphinVersion'):
        out.append(f"~ Wholphin: {before.get('wholphinVersion')} -> {after.get('wholphinVersion')}")
    for s in sorted(set(after.get('labScratch', [])) - set(before.get('labScratch', []))):
        out.append(f'+ file /data/local/tmp/{s}')
    return out


def restore_plan(before, now):
    """Commands (as adb argument lists) that put every touched value back."""
    plan = []
    for ns, key in TOUCHED:
        k = f'{ns}/{key}'
        if now['settings'].get(k) != before['settings'].get(k):
            old = before['settings'].get(k)
            plan.append(['shell', 'settings', 'delete', ns, key] if old is None else
                        ['shell', 'settings', 'put', ns, key, old])
    v0, v1 = before.get('musicVolume'), now.get('musicVolume')
    if v0 and v1 and v0['value'] != v1['value']:
        plan.append(['shell', 'cmd', 'media_session', 'volume', '--stream', str(MUSIC_STREAM), '--set', str(v0['value'])])
    for s in sorted(set(now.get('labScratch', [])) - set(before.get('labScratch', []))):
        plan.append(['shell', 'rm', '-f', '/data/local/tmp/' + s])
    return plan


def accepted_residue(before, after, *, kept_wholphin):
    """The only allowed lasting difference: Wholphin itself, if the harness installed it
    and the family chose to keep it (with its data cleared)."""
    allowed = set()
    if kept_wholphin and APP not in before['packages']:
        allowed |= {f"+ package {APP} {after['packages'].get(APP)}",
                    f"~ Wholphin: None -> {after.get('wholphinVersion')}"}
    return [line for line in diff(before, after) if line not in allowed]


# --------------------------------------------------------- capture adapter

def parse_v4l2_ctl(text):
    """`v4l2-ctl --list-formats-ext` -> [dict(fourcc, compressed, width, height, fps=[...])]."""
    modes, fmt, comp, size = [], None, False, None
    for line in text.splitlines():
        m = re.match(r"\s*\[\d+\]: '(\w{3,4})\s*' \(([^)]*)\)", line)
        if m:
            fmt, comp, size = m[1], 'compressed' in m[2].lower(), None
            continue
        m = re.match(r'\s*Size: (?:Discrete|Stepwise) (\d+)x(\d+)', line)
        if m and fmt:
            size = dict(fourcc=fmt, compressed=comp, width=int(m[1]), height=int(m[2]), fps=[])
            modes.append(size)
            continue
        m = re.match(r'\s*Interval: Discrete [\d.]+s \(([\d.]+) fps\)', line)
        if m and size is not None:
            size['fps'].append(round(float(m[1]), 3))
    return modes


FFMPEG_PIXFMT = {'YUYV': 'yuyv422', 'NV12': 'nv12', 'MJPG': 'mjpeg', 'YU12': 'yuv420p', 'UYVY': 'uyvy422'}
FOURCC = {v: k for k, v in FFMPEG_PIXFMT.items()}


def parse_ffmpeg_formats(text):
    """`ffmpeg -f v4l2 -list_formats all` -> same shape as parse_v4l2_ctl; frame rates unknown."""
    modes = []
    for m in re.finditer(r'(?m)\b(Raw|Compressed)\s*:\s*(\w+)\s*:.*:\s*((?:\d+x\d+\s*)+)$', text):
        for w, h in re.findall(r'(\d+)x(\d+)', m[3]):
            modes.append(dict(fourcc=FOURCC.get(m[2], m[2]), compressed=m[1] == 'Compressed',
                              width=int(w), height=int(h), fps=[]))
    return modes


def choose_mode(modes):
    """Prefer 1920x1080 at 60 fps, uncompressed over compressed. Returns (mode, reason) or (None, reason)."""
    full = [m for m in modes if (m['width'], m['height']) == (1920, 1080)]
    def at60(m): return any(abs(f - 60) < .5 for f in m['fps'])
    for want, why in (
        (lambda m: at60(m) and not m['compressed'], 'uncompressed 1920x1080 at 60 fps listed: no codec loss'),
        (lambda m: at60(m) and m['compressed'], 'no uncompressed 1080p60 listed; least-compressed 1080p60 mode (Motion-JPEG)'),
        (lambda m: not m['fps'] and not m['compressed'], 'uncompressed 1080p listed without frame rates; 60 fps requested and verified from the capture'),
        (lambda m: not m['fps'] and m['compressed'], 'compressed 1080p listed without frame rates; 60 fps requested and verified from the capture'),
    ):
        hit = [m for m in full if want(m)]
        if hit:
            order = ['YUYV', 'NV12', 'UYVY', 'YU12', 'MJPG']
            hit.sort(key=lambda m: order.index(m['fourcc']) if m['fourcc'] in order else 99)
            return hit[0], why
    return None, 'no 1920x1080 capture mode listed'


def parse_arecord_l(text):
    """`arecord -l` -> [dict(card, id, name, device)]."""
    return [dict(card=int(m[1]), id=m[2], name=m[3], device=int(m[4]))
            for m in re.finditer(r'(?m)^card (\d+): (\S+) \[([^\]]*)\], device (\d+):', text)]


def parse_asound_cards(text):
    """/proc/asound/cards -> [dict(card, id, driver, name)]."""
    return [dict(card=int(m[1]), id=m[2].strip(), driver=m[3].strip(), name=m[4].strip())
            for m in re.finditer(r'(?m)^\s*(\d+) \[([^\]]+)\]: (\S+) - (.*)$', text)]


def _usb_device(path):
    """Walk up a resolved sysfs device path to the USB device (the dir with idVendor)."""
    for p in (path, *path.parents):
        if (p/'idVendor').is_file() and (p/'idProduct').is_file():
            return p
    return None


def _read(p):
    try:
        return p.read_text().strip()
    except OSError:
        return ''


def discover(root=Path('/'), *, extra_ids=(), default_source_card=None):
    """List UVC video nodes and ALSA capture cards, pairing them by physical USB device.

    Returns dict(adapters=[...accepted pairs...], refused=[...with reasons...]).
    Nothing is opened; only sysfs and procfs are read.
    """
    ids = dict(ADAPTERS)
    ids.update({i.lower(): 'owner-named adapter' for i in extra_ids})
    usb, loose = {}, []
    for node in sorted((root/'sys/class/video4linux').glob('video*'), key=lambda p: int(p.name[5:] or 0)):
        dev = _usb_device((node/'device').resolve()) if (node/'device').exists() else None
        if dev is None:
            loose.append(dict(device=f"/dev/{node.name} {_read(node/'name')}", reason='not a USB device (virtual or built-in camera)'))
            continue
        entry = usb.setdefault(str(dev), dict(usb=f"{_read(dev/'idVendor')}:{_read(dev/'idProduct')}".lower(),
                                              product=_read(dev/'product'), video=[], cards=[]))
        entry['video'].append(dict(node='/dev/'+node.name, name=_read(node/'name'), index=_read(node/'index')))
    for card in sorted((root/'sys/class/sound').glob('card*')):
        if not card.name[4:].isdigit():
            continue
        dev = _usb_device((card/'device').resolve())
        if dev is None:
            continue
        n = int(card.name[4:])
        capture = sorted(p.name for p in (root/f'proc/asound/card{n}').glob('pcm*c'))
        entry = usb.setdefault(str(dev), dict(usb=f"{_read(dev/'idVendor')}:{_read(dev/'idProduct')}".lower(),
                                              product=_read(dev/'product'), video=[], cards=[]))
        entry['cards'].append(dict(card=n, id=_read(root/f'proc/asound/card{n}/id'), capture=capture))
    adapters, refused = [], loose
    for path, e in usb.items():
        label = f"{e['product'] or 'USB device'} [{e['usb']}]"
        words = (e['product'] + ' ' + ' '.join(v['name'] for v in e['video'])).casefold() + ' '
        if e['usb'] not in ids:
            why = 'webcam' if any(w in words for w in WEBCAM_WORDS) else 'not a known HDMI capture adapter'
            refused.append(dict(device=label, reason=why + ' (name it with --capture-usb VID:PID if it is one)'))
            continue
        video = [v for v in e['video'] if v['index'] in ('', '0')]
        cards = [c for c in e['cards'] if c['capture']]
        if default_source_card is not None:
            cards = [c for c in cards if c['card'] != default_source_card]
        if not video or not cards:
            refused.append(dict(device=label, reason='adapter lacks a capture video node or its own audio capture card'))
            continue
        adapters.append(dict(device=label, model=ids[e['usb']], usb=e['usb'], sysfs=path,
                             video=video[0]['node'], card=cards[0]['card'], cardId=cards[0]['id']))
    return dict(adapters=adapters, refused=refused)


def default_source_card(pactl_default_source, pactl_sources_json):
    """ALSA card number behind the desktop's default source (never used as the adapter)."""
    import json
    for s in json.loads(pactl_sources_json or '[]'):
        if s.get('name') == pactl_default_source:
            card = s.get('properties', {}).get('alsa.card') or s.get('properties', {}).get('api.alsa.card')
            return int(card) if str(card).isdigit() else None
    return None


# -------------------------------------------------------------- audio format

def iec61937(raw):
    """True if the s16le stereo PCM carries an IEC 61937 (bitstream) preamble: Pa=F872, Pb=4E1F."""
    sync = b'\x72\xf8\x1f\x4e'
    at = raw.find(sync)
    while at >= 0:
        if at % 4 == 0:
            return True
        at = raw.find(sync, at + 1)
    return False


def arriving_audio(probe):
    """From an ffprobe stream dict: is this decoded 48 kHz stereo 16-bit PCM?"""
    ok = (probe.get('codec_name') == 'pcm_s16le' and int(probe.get('sample_rate', 0)) == 48000
          and int(probe.get('channels', 0)) == 2)
    return ok, f"{probe.get('codec_name')} {probe.get('sample_rate')} Hz {probe.get('channels')} ch"


def env_without_secrets(env):
    return {k: v for k, v in env.items() if not k.startswith('CLEANYFIN_TV_')}


def media_state(dumpsys, package=APP):
    """Android PlaybackState of `package`'s media session from `dumpsys media_session`, or None."""
    current = False
    for line in dumpsys.splitlines():
        m = re.search(r'\bpackage=([\w.]+)', line)
        if m:
            current = m[1] == package
            continue
        # Android 14+ prints e.g. `state=PAUSED(2)`; older releases print `state=2`.
        m = re.search(r'\bstate=PlaybackState \{state=(?:[A-Z_]+\()?(-?\d+)', line)
        if m and current:
            return int(m[1])
    return None


def capture_files(values):
    """--capture-file [LABEL=]PATH (repeatable) -> {label or '*': Path}."""
    out = {}
    for v in values or ():
        label, _, path = v.rpartition('=') if re.match(r'^[A-Za-z]+=', v) else ('*', '', v)
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise ValueError(f'--capture-file {label}: not a file')
        out[label] = p
    return out


def pick_capture(mapping, label):
    return mapping.get(label) or mapping.get('*')


LABEL = re.compile(r'^[A-Za-z0-9](?:[A-Za-z0-9 :.,/_-]*[A-Za-z0-9:.,/_-])?$(?![\s\S])')  # evidence-register-v1 `label`


def register_label(text):
    """Fit free text into the evidence register's `label` alphabet and length."""
    out = re.sub(r' +', ' ', re.sub(r'[^A-Za-z0-9 :.,/_-]', ' ', text)).strip(' ,')[:256].rstrip(' ')
    out = re.sub(r'^[^A-Za-z0-9]+', '', out)
    if not LABEL.match(out):
        raise ValueError('device mode label cannot be expressed in the register alphabet')
    return out
