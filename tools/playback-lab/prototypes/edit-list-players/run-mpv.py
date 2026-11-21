#!/usr/bin/env python3
"""Capture and score the shared hard fixture through real mpv output drivers."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import wave

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
MEDIA_IMAGE = 'jellyfin/jellyfin@sha256:aefb67e6a7ff1debdd154a78a7bbb780fd0c873d8639210a7f6a2016ad2b35db'


def lab_script(name, *args, **kwargs):
    return subprocess.run([sys.executable, str(LAB / name), *map(str, args)], **kwargs)


def make_fixture(work):
    subprocess.run(['docker', 'image', 'inspect', MEDIA_IMAGE], stdout=subprocess.DEVNULL, check=True)
    fixture = work / 'fixture'
    lab_script('hard-fixture.py', fixture, check=True)
    return fixture, json.loads((fixture / 'plan.json').read_text())


def image_id(image):
    details = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], text=True))[0]
    print(f"Image {details['Id']} ({details['Size']} bytes)", flush=True)
    return details['Id']


def container(image, work, *args, entrypoint, timeout=90, **kwargs):
    command = ['docker', 'run', '--rm', '--pull=never', '--network', 'none',
               '--user', f'{os.getuid()}:{os.getgid()}', '-e', 'HOME=/tmp',
               '--entrypoint', entrypoint]
    cidfile = None
    if work is not None:
        # Put the cidfile beside the mounted directory; it tracks cleanup on timeout.
        cidfile = work.parent / 'container.cid'
        cidfile.unlink(missing_ok=True)
        command += ['--cidfile', str(cidfile), '-v', f'{work}:/lab:Z', '-w', '/lab']
    try:
        return subprocess.run([*command, image, *args], text=True, timeout=timeout, **kwargs)
    finally:
        if cidfile is not None and cidfile.exists():
            subprocess.run(['docker', 'rm', '-f', cidfile.read_text().strip()],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            cidfile.unlink()


def score(fixture, label, *, video=None, audio=None):
    args = ['--plan', fixture / 'plan.json', '--json']
    if video is not None:
        args += ['--video', video]
    if audio is not None:
        args += ['--audio', audio]
    result = lab_script('analyze-capture.py', *args, capture_output=True, text=True, timeout=90)
    print(f'\n{label}', flush=True)
    print(result.stdout, end='', flush=True)
    if result.stderr:
        print(result.stderr, end='', file=sys.stderr)
    metrics = json.loads(result.stdout.splitlines()[-1])
    if 'error' in metrics:
        raise RuntimeError(metrics['error'])
    return metrics


def mpv(image, fixture, source, *options):
    return container(image, fixture, '--no-config', '--no-terminal', '--input-default-bindings=no',
                     '--load-scripts=no', *options, '--', source, entrypoint='mpv',
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def capture(image, fixture, label, source, *, video=True, audio=True, scripted=False, options=()):
    frames, wav = fixture / (label + '-frames'), fixture / (label + '.wav')
    common = ('--hwdec=no', '--hr-seek=yes', '--audio-file-auto=no', '--sub-auto=no',
              '--untimed=' + ('no' if scripted else 'yes'))
    png = ('--vo=image', '--vo-image-format=png', '--vo-image-png-compression=1',
           f'--vo-image-outdir={frames.name}')
    pcm_options = ('--ao=pcm', '--ao-pcm-waveheader=yes', f'--ao-pcm-file={wav.name}',
                   '--audio-samplerate=48000', '--audio-channels=stereo', '--audio-format=s16')
    streams = []
    if video:
        frames.mkdir()
    if scripted:
        # PCM alone races to EOF before the Lua observer sees intermediate time-pos.
        # Exercise the same seek sequence on both tracks in one A/V playback run.
        streams.append(('av', (*png, *pcm_options)))
    else:
        if video:
            streams.append(('video', ('--no-audio', *png)))
        if audio:
            streams.append(('audio', ('--no-video', *pcm_options)))
    for kind, driver in streams:
        log_name = label + '-' + kind + '.log'
        result = mpv(image, fixture, source, f'--log-file={log_name}', *common, *driver, *options)
        log = (fixture / log_name).read_text() if (fixture / log_name).exists() else result.stdout
        if result.returncode != 0:
            raise RuntimeError(f'{label}/{kind}: mpv exited {result.returncode}\n{log[-4000:]}')
        if label == 'seek':
            relevant = [line.split('[seek]')[-1].strip() for line in log.splitlines() if 'SEEK-' in line]
            print(f'{label}/{kind}: ' + '; '.join(relevant), flush=True)
            if not any('SEEK-BACK-LANDED' in line for line in relevant):
                raise RuntimeError(f'{label}/{kind}: seek script did not finish')
    return score(fixture, label, video=frames if video else None, audio=wav if audio else None)


def write_seek(fixture, splice, before):
    # The EDL exposes only edited time. An application mapping a removed source
    # time clamps it to the splice; 8.000 on the edited clock is *not* source 8.000.
    (fixture / 'seek.lua').write_text(f'''
local state = 0
local function seek(label, target)
    mp.msg.warn(label .. string.format(" from=%.3f target=%.3f", mp.get_property_number("time-pos"), target))
    mp.commandv("seek", target, "absolute+exact")
end
mp.observe_property("time-pos", "number", function(_, t)
    if not t then return end
    if state == 0 and t >= {before - 0.08:.3f} and t < {splice:.3f} then
        state = 1
        seek("SEEK-SOURCE-8.000-CLAMPED", {splice:.3f})
    elseif state == 2 and t >= {splice + 0.40:.3f} then
        state = 3
        seek("SEEK-BACK", {before:.3f})
    end
end)
mp.register_event("playback-restart", function()
    if state == 1 then
        state = 2
        mp.msg.warn(string.format("SEEK-FORWARD-LANDED %.3f", mp.get_property_number("time-pos")))
    elseif state == 3 then
        state = 4
        mp.msg.warn(string.format("SEEK-BACK-LANDED %.3f", mp.get_property_number("time-pos")))
    end
end)
''')


def pcm(fixture, label):
    with wave.open(str(fixture / (label + '.wav')), 'rb') as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (48000, 2, 2):
            raise RuntimeError(f'{label}: expected 48 kHz stereo signed 16-bit WAV')
        return wav.readframes(wav.getnframes())


def compare_pcm(fixture, plan):
    cut, = plan['cuts']
    start, end = [cut[k] * 48 * 4 for k in ('startMs', 'endMs')]
    duration = plan['source']['frames'] * 48000 // plan['source']['fps'] * 4
    source, cut_pcm, muted = (pcm(fixture, name) for name in ('control', 'cut', 'mute'))
    print(f'PCM frames: source {len(source)//4}, cut {len(cut_pcm)//4}, mute {len(muted)//4}', flush=True)
    trim = source[:start] + source[end:duration]
    differences = [i // 4 for i in range(0, min(len(trim), len(cut_pcm)), 4)
                   if trim[i:i+4] != cut_pcm[i:i+4]]
    extent = f', first {differences[0]}, last {differences[-1]}' if differences else ''
    print(f'Cut vs trimmed control: {len(differences)} unequal overlapping PCM frames{extent}; '
          f'length delta {(len(cut_pcm)-len(trim))//4} frames', flush=True)
    print(f'Mute: {sum(muted[i:i+4] != bytes(4) for i in range(start, min(end, len(muted)), 4))} '
          'nonzero PCM frames in the marked interval', flush=True)


def failures(image, fixture):
    (fixture / 'malformed.mpv.edl').write_text('# mpv EDL v0\nsource.mp4,not-a-time,5.480\n')
    for name in ('missing', 'malformed'):
        log = name + '.log'
        wav = name + '.wav'
        result = mpv(image, fixture, name + '.mpv.edl', f'--log-file={log}', '--no-video',
                     '--untimed', '--ao=pcm', f'--ao-pcm-file={wav}')
        output = (fixture / log).read_text() if (fixture / log).exists() else result.stdout
        errors = [line for line in output.splitlines() if '[e]' in line or '[f]' in line]
        print(f'\n{name}: exit={result.returncode}; PCM file={ (fixture / wav).exists() }\n' + '\n'.join(errors), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='cleanyfin-edl-lab:mpv')
    args = parser.parse_args()
    image = image_id(args.image)
    container(image, None, '--version', entrypoint='mpv', check=True)
    container(image, None, '-W', 'mpv', entrypoint='dpkg-query', check=True)
    with tempfile.TemporaryDirectory(prefix='cleanyfin-mpv-') as work:
        work = Path(work)
        fixture, plan = make_fixture(work)
        control = capture(image, fixture, 'control', 'source.mp4')
        if (control['video']['markedCount'] != 126 or control['video']['frames'] != 500
                or control['video']['missingCount'] or control['video']['unreadableCount']
                or control['video']['duplicateCount'] or not control['video']['monotonic']
                or abs(control['audio']['leakMs'] - 5040) > 5):
            raise RuntimeError('Invalid capture method: unedited control did not show the full fixture')
        capture(image, fixture, 'cut', 'fixture.mpv.edl')
        cut, = plan['cuts']
        splice = cut['startMs'] / 1000
        before = splice - 0.20
        capture(image, fixture, 'start', 'fixture.mpv.edl', options=(f'--start={before:.3f}',))
        write_seek(fixture, splice, before)
        capture(image, fixture, 'seek', 'fixture.mpv.edl', scripted=True,
                options=(f'--start={before - 0.32:.3f}', '--script=seek.lua'))
        gate = f'lt(n,{cut["startMs"]*48})+gte(n,{cut["endMs"]*48})'
        af = f"--af=lavfi=[aeval=exprs='val(0)*({gate})|val(1)*({gate})':channel_layout=stereo]"
        capture(image, fixture, 'mute', 'source.mp4', video=False, options=(af,))
        compare_pcm(fixture, plan)
        failures(image, fixture)
        print(f'Temporary on-disk artifacts: {sum(p.stat().st_blocks*512 for p in work.rglob("*") if p.is_file())} bytes', flush=True)


if __name__ == '__main__':
    main()
