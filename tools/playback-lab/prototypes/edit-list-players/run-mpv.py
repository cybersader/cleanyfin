#!/usr/bin/env python3
"""Capture and score the shared hard fixture through real mpv output drivers."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(LAB))
from exact_audio import BYTES_PER_FRAME, compare_cut, plan_frames, read_pcm, stereo_samples, summary
from hard_fixture_lab import IMAGE as MEDIA_IMAGE


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


def score(fixture, label, *, video=None, audio=None, diagnostic=False):
    args = ['--plan', fixture / 'plan.json', '--json']
    if video is not None:
        args += ['--video', video]
    if audio is not None:
        args += ['--audio', audio]
    result = lab_script('analyze-capture.py', *args, capture_output=True, text=True, timeout=90)
    title = 'uncut positive control' if label == 'control' else f'{label} diagnostic' if diagnostic else label
    print(f'\n{title}', flush=True)
    # The all-kept cut verdict does not apply to the positive control or the
    # start/seek/mute diagnostics; always show the measured frame and tone counts.
    lines = result.stdout.splitlines()
    print('\n'.join(lines[:-2]), flush=True)
    if result.stderr:
        print(result.stderr, end='', file=sys.stderr)
    metrics = json.loads(lines[-1])
    if 'error' in metrics:
        raise RuntimeError(metrics['error'])
    return metrics


def mpv(image, fixture, source, *options):
    return container(image, fixture, '--no-config', '--no-terminal', '--input-default-bindings=no',
                     '--load-scripts=no', *options, '--', source, entrypoint='mpv',
                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def capture(image, fixture, label, source, *, scripted=False, options=()):
    frames, wav = fixture / (label + '-frames'), fixture / (label + '.wav')
    frames.mkdir()
    log_name = label + '-av.log'
    result = mpv(image, fixture, source, f'--log-file={log_name}', '--hwdec=no', '--hr-seek=yes',
                 '--audio-file-auto=no', '--sub-auto=no', '--framedrop=no',
                 '--untimed=' + ('no' if scripted else 'yes'), '--vo=image', '--vf=scale=160:90',
                 '--vo-image-format=png', '--vo-image-png-compression=1', f'--vo-image-outdir={frames.name}',
                 '--ao=pcm', '--ao-pcm-waveheader=yes', f'--ao-pcm-file={wav.name}',
                 '--audio-samplerate=48000', '--audio-channels=stereo', '--audio-format=s16', *options)
    log = (fixture / log_name).read_text() if (fixture / log_name).exists() else result.stdout
    if result.returncode != 0:
        raise RuntimeError(f'{label}/av: mpv exited {result.returncode}\n{log[-4000:]}')
    if scripted:
        relevant = [line.split('[seek]')[-1].strip() for line in log.splitlines() if 'SEEK-' in line]
        print(f'{label}/av: ' + '; '.join(relevant), flush=True)
        if not any('SEEK-BACK-LANDED' in line for line in relevant):
            raise RuntimeError(f'{label}/av: seek script did not finish')
    return score(fixture, label, video=frames, audio=wav, diagnostic=label != 'cut')


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



def failures(image, fixture):
    (fixture / 'malformed.mpv.edl').write_text('# mpv EDL v0\nsource.mp4,not-a-time,5.480\n')
    for name in ('missing', 'malformed'):
        log = name + '.log'
        wav = name + '.wav'
        result = mpv(image, fixture, name + '.mpv.edl', f'--log-file={log}', '--vo=null',
                     '--untimed', '--ao=pcm', f'--ao-pcm-file={wav}')
        output = (fixture / log).read_text() if (fixture / log).exists() else result.stdout
        errors = [line for line in output.splitlines() if '[e]' in line or '[f]' in line]
        print(f'\n{name}: exit={result.returncode}; PCM file={ (fixture / wav).exists() }\n' + '\n'.join(errors), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='cleanyfin-edl-lab:mpv')
    parser.add_argument('--receipt', type=Path, help='write the measured straight-cut and control results as JSON')
    args = parser.parse_args()
    image = image_id(args.image)
    version = container(image, None, '--version', entrypoint='mpv', check=True, capture_output=True).stdout
    print(version, end='', flush=True)
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
        cut_metrics = capture(image, fixture, 'cut', 'fixture.mpv.edl')
        pcm = compare_cut(plan, read_pcm(fixture/'control.wav'), read_pcm(fixture/'cut.wav'))
        print(f'{"PASS" if pcm["exact"] else "FAIL"}: joint cut PCM: {summary(pcm)}', flush=True)
        pictures, tones = cut_metrics['video'], cut_metrics['audio']
        pictures_exact = pictures['pass'] and pictures['frames'] == 374 and not pictures['duplicateCount']
        print(f'{"PASS" if tones["pass"] else "FAIL"}: joint cut tone-window cross-check', flush=True)
        if not (pcm['exact'] and pictures_exact and tones['pass']):
            raise RuntimeError('Joint straight-cut result differs from the research result')
        print(f'PASS: joint AAC cut: {pictures["frames"]} kept pictures, {pictures["markedCount"]} marked pictures, '
              f'0 marked samples (exact kept PCM), {tones["leakMs"]:g} ms marked tone', flush=True)
        cut, = plan['cuts']
        splice = cut['startMs'] / 1000
        before = splice - 0.20
        capture(image, fixture, 'start', 'fixture.mpv.edl', options=(f'--start={before:.3f}',))
        write_seek(fixture, splice, before)
        seek = capture(image, fixture, 'seek', 'fixture.mpv.edl', scripted=True,
                options=(f'--start={before - 0.32:.3f}', '--script=seek.lua'))
        gate = f'lt(n,{cut["startMs"]*48})+gte(n,{cut["endMs"]*48})'
        af = f"--af=lavfi=[aeval=exprs='val(0)*({gate})|val(1)*({gate})':channel_layout=stereo]"
        capture(image, fixture, 'mute', 'source.mp4', options=(af,))
        start, end, _ = plan_frames(plan)
        muted = read_pcm(fixture/'mute.wav')[start*BYTES_PER_FRAME:end*BYTES_PER_FRAME]
        nonzero = sum(sample != bytes(BYTES_PER_FRAME) for sample in stereo_samples(muted))
        print(f'Mute diagnostic: {nonzero} nonzero PCM samples in the marked interval', flush=True)
        if args.receipt:
            args.receipt.write_text(json.dumps({
                'method': 'One mpv playback per take, image + 48 kHz stereo s16le PCM, '
                          'uninterrupted decode minus marked source sample indices; no alignment',
                'mpvImage': image, 'mpvVersion': version.splitlines()[0].split()[1],
                'ffmpegVersion': version.splitlines()[2].removeprefix('FFmpeg version: '),
                'fixtureImage': MEDIA_IMAGE,
                'sourceSHA256': plan['source']['sha256'], 'sourceCuts': plan['cuts'],
                'control': {'pictures': control['video']['frames'], 'markedPictures': control['video']['markedCount'],
                            'markedToneMs': control['audio']['leakMs']},
                'straightCut': {'picture': pictures, 'toneWindows': tones, 'pcm': pcm},
                'seekDiagnostic': {'markedPictures': seek['video']['markedCount'],
                                   'markedToneMs': seek['audio']['leakMs']},
            }, indent=2) + '\n')
        failures(image, fixture)
        print(f'Temporary on-disk artifacts: {sum(p.stat().st_blocks*512 for p in work.rglob("*") if p.is_file())} bytes', flush=True)


if __name__ == '__main__':
    main()
