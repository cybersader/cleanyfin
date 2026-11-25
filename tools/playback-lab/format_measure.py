"""Joint picture + decoded-sound measurement of one layout through real desktop mpv (lab only).

One playback per take, with picture and sound selected together as a viewer would play it: numbered images and
48 kHz signed 16-bit PCM in the layout's own channel count. The exact verdict compares every channel of every sample
with the same mpv's uninterrupted decode of the same file, minus exactly the marked sample indices; no alignment,
resampling or tolerance. Pictures are read from the barcode, so every kept frame must appear once and in order and
no marked frame may appear at all.

Clock: a plan's source time zero is the earliest audio/video first timestamp (the edition rule, and what mpv rebases
to). mpv starts sound at the first picture, so output sample k of an uninterrupted play is source time
videoAnchorMs + k/48 ms. Marked samples are therefore [ (startMs - anchor)*48, (endMs - anchor)*48 ).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

from exact_audio import RATE, compare, compare_ranges, kept_pcm, read_pcm, summary
from format_fixtures import END, FPS, FRAMES, START

LAB = Path(__file__).resolve().parent
MPV = 'cleanyfin-edl-lab:mpv'
FRAME_MS = 1000//FPS
SAMPLES_PER_FRAME = RATE//FPS
CHANNEL_LAYOUT = {2: 'stereo', 6: '5.1(side)'}
SUB_SCRIPT = '''
local was_text = false
mp.observe_property("sub-text", "string", function(_, t)
    local at = string.format("%.3f", mp.get_property_number("time-pos") or -1)
    if t and t ~= "" then
        mp.msg.warn("SUBTEXT " .. t:gsub("\\n", " ") .. " @" .. at)
        was_text = true
    elseif was_text then
        mp.msg.warn("SUBCLEAR " .. at)
        was_text = false
    end
end)
mp.register_event("file-loaded", function()
    local tracks = {}
    for _, t in ipairs(mp.get_property_native("track-list") or {}) do
        if t.type == "sub" then
            table.insert(tracks, {id=t.id, codec=t.codec, default=t.default, forced=t.forced, selected=t.selected})
        end
    end
    mp.msg.warn("SUBTRACKS " .. require("mp.utils").format_json(tracks))
end)
mp.observe_property("current-tracks/audio/id", "number", function(_, id) if id then mp.msg.warn("AUDIO-TRACK " .. id) end end)
'''


class Unmeasured(Exception):
    """A take that did not finish (timeout or player failure): never a pass."""


def clocks(probe):
    """The plugin's edition clock (EditionLayout.Clocks) from ffprobe JSON: anchors in whole ms, or ValueError."""
    av = [s for s in probe['streams'] if s['codec_type'] in ('video', 'audio')]
    start = {s['index']: Fraction(s['start_pts'])*Fraction(s['time_base']) for s in av}
    zero = min(start.values())
    anchors = {}
    for s in av:
        ms = (start[s['index']] - zero)*1000
        if ms.denominator != 1:
            raise ValueError(f'stream {s["index"]} does not start a whole millisecond after source zero')
        anchors[s['index']] = int(ms)
    duration_us = round(Fraction(probe['format']['duration'])*1_000_000)
    zero_ms = zero*1000
    if zero_ms.denominator != 1:
        raise ValueError('source zero is not a whole millisecond of file time')
    return anchors, (duration_us + 999)//1000, int(zero_ms)


def plan_for(probe):
    """The mark that removes exactly source frames [137, 263) on this file's own clock, as whole source ms."""
    anchors, duration, zero = clocks(probe)
    video = next(s['index'] for s in probe['streams'] if s['codec_type'] == 'video')
    av = anchors[video]
    return dict(videoAnchorMs=av, startMs=av + START*FRAME_MS, endMs=av + END*FRAME_MS, durationMs=duration,
                anchors=anchors, zeroMs=zero)


def seconds(us):
    """Microseconds as mpv EDL seconds (exact decimal)."""
    sign, us = ('-', -us) if us < 0 else ('', us)
    return f'{sign}{us//1_000_000}.{us%1_000_000:06}'


def edl_body(source, cut, file_clock=True, clamp=True, nudge_us=0, split=0):
    """Kept ranges in mpv EDL form. mpv reads segment times as the file's own timestamps, so a source-clock plan is
    shifted by source zero (the file time of source 0); file_clock=False reproduces the old unshifted renderer.
    nudge_us moves every interior boundary earlier, off the exact frame/sample instant."""
    z = cut['zeroMs'] if file_clock else 0
    first = (max(z, 0) if clamp else z)*1000

    def part(shift):
        end1 = (cut['startMs'] + z)*1000 + shift
        start2 = (cut['endMs'] + z)*1000 + shift
        return (f'{source},{seconds(first)},{seconds(end1 - first)}\n'
                f'{source},{seconds(start2)},{seconds((cut["durationMs"] + z)*1000 - start2)}\n')
    if split:
        # Picture boundaries just before the frame instant, sound boundaries just after the sample instant, each in
        # its own partition of the same source; the kept frames and samples are the same sets.
        return ('# mpv EDL v0\n!no_chapters\n!delay_open,media_type=video\n' + part(-split) +
                '!new_stream\n!delay_open,media_type=audio\n' + part(split))
    return '# mpv EDL v0\n!no_chapters\n' + part(-nudge_us)


class Runner:
    """Runs mpv in the lab image: --network none for local files, or inside a lab server's loopback namespace."""

    def __init__(self, work, network='none', timeout=240, mounts=()):
        self.work, self.network, self.timeout, self.mounts = Path(work), network, timeout, list(mounts)
        (self.work/'subs.lua').write_text(SUB_SCRIPT)

    def mpv(self, source, args):
        """source: a path inside the container or a URL, or a callable returning a fresh one for this take.
        It is passed on stdin, never on a command line, so a capability URL stays out of process listings."""
        source = source() if callable(source) else source
        cid = self.work/'mpv.cid'
        cid.unlink(missing_ok=True)
        cmd = ['docker', 'run', '-i', '--rm', '--pull=never', '--network', self.network, '--cidfile', str(cid),
               '--user', f'{os.getuid()}:{os.getgid()}', '-e', 'HOME=/tmp', '-v', f'{self.work}:/lab:z', *self.mounts,
               '-w', '/lab', '--entrypoint', 'sh', MPV, '-c',
               'IFS= read -r src; exec mpv --no-config --no-terminal --load-scripts=no --input-default-bindings=no '
               '--hwdec=no --audio-file-auto=no --sub-auto=no --framedrop=no --hr-seek=yes "$@" -- "$src"', 'mpv', *args]
        try:
            return subprocess.run(cmd, input=(source+'\n').encode(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=self.timeout).stdout.decode(errors='replace')
        except subprocess.TimeoutExpired as exc:
            raise Unmeasured(f'mpv timed out after {self.timeout} s') from exc
        finally:
            if cid.exists():
                subprocess.run(['docker', 'rm', '-f', cid.read_text().strip()], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30)
                cid.unlink(missing_ok=True)

    def capture(self, label, source, channels, *extra):
        frames, wav, log = self.work/f'{label}-frames', self.work/f'{label}.wav', self.work/f'{label}.log'
        shutil.rmtree(frames, ignore_errors=True)
        frames.mkdir()
        for f in (wav, log):
            f.unlink(missing_ok=True)
        self.mpv(source, ['--untimed=yes', '--vo=image', '--vf=scale=160:90', '--vo-image-format=png',
                          '--vo-image-png-compression=1', f'--vo-image-outdir={frames.name}', '--ao=pcm',
                          '--ao-pcm-waveheader=yes', f'--ao-pcm-file={wav.name}', f'--audio-samplerate={RATE}',
                          f'--audio-channels={CHANNEL_LAYOUT[channels]}', '--audio-format=s16', '--msg-level=all=warn',
                          f'--log-file={log.name}', '--script=subs.lua', *extra])
        if not wav.exists() or not any(frames.iterdir()):
            raise Unmeasured(f'{label}: no picture or sound captured')
        text = log.read_text(errors='replace') if log.exists() else ''
        subs = [l.split('SUBTEXT ', 1)[1] for l in text.splitlines() if 'SUBTEXT ' in l]
        tracks = sorted({int(l.rsplit(' ', 1)[1]) for l in text.splitlines() if 'AUDIO-TRACK ' in l})
        clears = [float(l.rsplit(' ', 1)[1]) for l in text.splitlines() if 'SUBCLEAR ' in l]
        sub_tracks = [json.loads(l.split('SUBTRACKS ', 1)[1]) for l in text.splitlines() if 'SUBTRACKS ' in l]
        return frames, wav, dict(subtitles=subs, subtitleClears=clears, subtitleTracks=(sub_tracks or [[]])[-1], audioTracks=tracks)

    def frames(self, directory, plan_path):
        r = subprocess.run([sys.executable, str(LAB/'analyze-capture.py'), '--plan', str(plan_path), '--video',
                            str(directory), '--json'], capture_output=True, text=True, timeout=self.timeout)
        report = json.loads(r.stdout.splitlines()[-1])
        if 'error' in report:
            raise Unmeasured(report['error'])
        return report['video']

    def tones(self, wav, plan_path):
        r = subprocess.run([sys.executable, str(LAB/'analyze-capture.py'), '--plan', str(plan_path), '--audio',
                            str(wav), '--json'], capture_output=True, text=True, timeout=self.timeout)
        report = json.loads(r.stdout.splitlines()[-1])
        if 'error' in report:
            raise Unmeasured(report['error'])
        return report['audio']

    def sequence(self, directory):
        """Source frame numbers of the captured pictures, in capture order (unreadable as None)."""
        return read_sequence(directory)


def _analyzer():
    import importlib.util
    spec = importlib.util.spec_from_file_location('analyze_capture', LAB/'analyze-capture.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_sequence(directory):
    a = _analyzer()
    from hard_fixture_lab import SCAN_HEIGHT, SCAN_WIDTH, media
    raw = media(directory, 'ffmpeg', '-protocol_whitelist', 'file,pipe', '-f', 'concat', '-safe', '0', '-i', 'pipe:0',
                '-map', '0:v:0', '-vf', f'scale={SCAN_WIDTH}:{SCAN_HEIGHT}:flags=area,format=gray,settb=1/{FPS},setpts=N',
                '-fps_mode', 'passthrough', '-enc_time_base:v', 'filter', '-threads:v', '1', '-f', 'rawvideo', 'pipe:1',
                readonly=True, data=a.image_manifest(directory))
    size = SCAN_WIDTH*SCAN_HEIGHT
    return [a.read_barcode(memoryview(raw)[o:o+size], FRAMES) for o in range(0, len(raw), size)]


def read_one(image):
    from hard_fixture_lab import SCAN_HEIGHT, SCAN_WIDTH, media
    raw = media(image.parent, 'ffmpeg', '-i', f'/lab/{image.name}', '-vf',
                f'scale={SCAN_WIDTH}:{SCAN_HEIGHT}:flags=area,format=gray', '-frames:v', '1', '-f', 'rawvideo', 'pipe:1',
                readonly=True)
    return _analyzer().read_barcode(memoryview(raw), FRAMES)


def edited_frame(source_frame):
    return source_frame if source_frame < START else source_frame - (END-START)


def measure(runner, *, source, control_source, probe, channels, plan_path, mpv_options=(), starts=(5.28, 5.48, 7.0)):
    """Control, straight cut and fresh starts. Returns a result dict; raises Unmeasured if a take did not finish."""
    cut = plan_for(probe)
    av = cut['videoAnchorMs']
    size = 2*channels
    result = dict(cut=cut, channels=channels, mpvOptions=list(mpv_options))
    frames, wav, extra = runner.capture('control', control_source, channels, *mpv_options)
    v = runner.frames(frames, plan_path)
    control = read_pcm(wav, channels)
    result['control'] = dict(frames=v['frames'], markedCount=v['markedCount'], missing=v['missingCount'],
                             unreadable=v['unreadableCount'], duplicates=v['duplicateCount'],
                             samples=len(control)//size, **extra)
    if v['frames'] != FRAMES or v['markedCount'] != END-START or v['missingCount'] or v['unreadableCount']:
        raise Unmeasured('uncut control did not show all 500 pictures including 126 marked')
    marked = ((cut['startMs']-av)*48, (cut['endMs']-av)*48)
    # Up to the last planned sample, not codec padding after it.
    kept = [(0, marked[0]), (marked[1], min(len(control)//size, (cut['durationMs'] - av)*48))]
    expected = kept_pcm(control, kept, channels)

    frames, wav, extra = runner.capture('cut', source, channels, *mpv_options)
    v = runner.frames(frames, plan_path)
    pcm = compare(expected, read_pcm(wav, channels), marked[0], channels)
    t = runner.tones(wav, plan_path)
    result['straight'] = dict(picture=dict(frames=v['frames'], markedCount=v['markedCount'], missing=v['missingCount'],
                                           duplicates=v['duplicateCount'], unreadable=v['unreadableCount'],
                                           monotonic=v['monotonic'], ok=v['pass'] and v['frames'] == FRAMES-(END-START)
                                           and not v['duplicateCount']),
                              sound=pcm | dict(summary=summary(pcm, 'sample frames')),
                              tone=dict(markedToneMs=t['leakMs'], ok=t['pass']), **extra)
    cut_pcm = read_pcm(wav, channels)
    result['starts'] = []
    for at in starts:
        frames, wav, start_extra = runner.capture(f'start-{at}', source, channels, f'--start={at:.3f}', *mpv_options)
        seq = runner.sequence(frames)
        readable = [f for f in seq if f is not None]
        first = readable[0] if readable else None
        e = edited_frame(first) if first is not None else None
        tail = cut_pcm[e*SAMPLES_PER_FRAME*size:] if e is not None else b''
        got = read_pcm(wav, channels)
        pcm = compare(tail, got, 0, channels)
        want = [f for f in list(range(START)) + list(range(END, FRAMES)) if e is not None and edited_frame(f) >= e]
        # A known mpv deviation: a fresh start can emit extra samples from before the start position. Say which
        # source samples they are, so an extra sample from inside the mark is never mistaken for a kept one.
        lead = None
        if not pcm['exact'] and e is not None:
            lead = next((k for k in range(1, 9) if got[k*size:] == tail), None)
        source_index = (first if first is not None and first < START else (first or 0)) * SAMPLES_PER_FRAME
        extra = dict(leadingExtraSamples=lead,
                     extraSourceSamples=[source_index - lead, source_index] if lead else None,
                     extraMatchesSource=bool(lead) and got[:lead*size] == control[(source_index-lead)*size:source_index*size],
                     extraMarked=bool(lead) and any(marked[0] <= i < marked[1] for i in range(source_index-lead, source_index)))
        if lead and cut['zeroMs'] == 0 and av == 0:
            # Is it the player's start behaviour? Start the unfiltered original at the same source frame.
            _, owav, _ = runner.capture(f'original-start-{at}', control_source, channels,
                                        f'--start={first*FRAME_MS/1000:.3f}', *mpv_options)
            original = read_pcm(owav, channels)
            extra['originalStartLead'] = next((k for k in range(0, 9) if original[k*size:k*size+SAMPLES_PER_FRAME*size]
                                               == control[source_index*size:(source_index+SAMPLES_PER_FRAME)*size]), None)
        result['starts'].append(dict(startSeconds=at, firstFrame=first, markedCount=sum(START <= f < END for f in readable),
                                     pictureOk=readable == want and len(readable) == len(seq),
                                     sound=pcm | dict(summary=summary(pcm, 'sample frames')), **extra, **start_extra))
    return result


def seeks(runner, source, mpv_options=(), targets=None, switch_sid=None):
    """Paused desktop mpv with exact landing pictures and active text, optionally after an in-player track switch."""
    if targets is None:
        # loadfile; forward across the join; exactly onto it; back across it; onto the last kept frame; 1 ms before the
        # join (mpv shows the first frame at or after a target, so the next kept frame, never a marked one).
        targets = [(None, 0), (7.0, 301), (START*FRAME_MS/1000, END), (2.0, 50), ((START-1)*FRAME_MS/1000, START-1),
                   (START*FRAME_MS/1000 - 0.001, END)]
    w = runner.work
    images = w/'seek-images'
    shutil.rmtree(images, ignore_errors=True)
    images.mkdir()
    sock = w/'seek.sock'
    sock.unlink(missing_ok=True)
    cid = subprocess.check_output(['docker', 'run', '-d', '--rm', '--pull=never', '--network', runner.network,
        '--user', f'{os.getuid()}:{os.getgid()}', '-e', 'HOME=/tmp', '-v', f'{w}:/lab:z', *runner.mounts, '-w', '/lab', '--entrypoint=mpv',
        MPV, '--no-config', '--load-scripts=no', '--sub-auto=no', '--audio-file-auto=no', '--pause', '--idle', '--keep-open',
        '--hwdec=no', '--framedrop=no', '--hr-seek=yes', '--no-input-terminal', '--really-quiet', '--vo=image',
        '--vo-image-format=png', '--vf=scale=160:90', '--vo-image-outdir=seek-images', '--ao=null',
        '--input-ipc-server=/lab/seek.sock', *mpv_options], text=True, timeout=60).strip()
    landings = []
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            fd = os.open(w, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for _ in range(200):
                    try:
                        s.connect(f'/proc/self/fd/{fd}/seek.sock')
                        break
                    except (FileNotFoundError, ConnectionRefusedError):
                        time.sleep(.05)
                else:
                    raise Unmeasured('seek socket did not become ready')
            finally:
                os.close(fd)
            s.settimeout(min(30, runner.timeout))
            stream = s.makefile('rb')
            request_id = 0

            def send(command):
                nonlocal request_id
                request_id += 1
                s.sendall((json.dumps({'command': command, 'request_id': request_id})+'\n').encode())
                return request_id

            def wait_for(predicate):
                deadline = time.monotonic() + min(30, runner.timeout)
                while time.monotonic() < deadline:
                    try:
                        line = stream.readline()
                    except (OSError, TimeoutError):
                        break
                    if not line:
                        break
                    value = json.loads(line)
                    if predicate(value):
                        return value
                raise Unmeasured('timed out waiting for mpv seek IPC')

            def get(name):
                request = send(['get_property', name])
                return wait_for(lambda val: val.get('request_id') == request).get('data')

            for target, expected in targets:
                before = set(images.glob('*.png'))
                send(['loadfile', source] if target is None else ['seek', target, 'absolute+exact'])
                event = wait_for(lambda val: val.get('event') in ('playback-restart', 'end-file'))
                if event['event'] == 'end-file':
                    raise Unmeasured('mpv ended before a seek landing')
                time.sleep(.3)
                fresh = sorted(set(images.glob('*.png')) - before)
                got = read_one(fresh[-1]) if fresh else None
                landings.append(dict(target=target, expected=expected, landed=got,
                                     marked=bool(fresh) and any(START <= (read_one(f) or -1) < END for f in fresh),
                                     subtitle=get('sub-text'), sid=get('sid')))
                if target is None and switch_sid is not None:
                    request = send(['set_property', 'sid', switch_sid])
                    if wait_for(lambda val: val.get('request_id') == request).get('error') != 'success' or get('sid') != switch_sid:
                        raise Unmeasured('mpv refused the requested in-player subtitle switch')
    finally:
        subprocess.run(['docker', 'rm', '-f', cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        sock.unlink(missing_ok=True)
        shutil.rmtree(images, ignore_errors=True)
    return dict(landings=landings, ok=all(l['landed'] == l['expected'] and not l['marked'] for l in landings))


def verdict(result):
    """Exact only if every claim measured exact; any deviation is stated, never rounded away."""
    s = result['straight']

    def start_ok(x):
        # Exact from the start position on. Extra leading samples pass only if they are the kept source samples just
        # before it AND the unfiltered original started at the same frame emits the same number (player start
        # behaviour, not filtering). Any marked sample fails.
        sound = x['sound']['exact'] or (x.get('leadingExtraSamples') and x.get('extraMatchesSource') and not x.get('extraMarked')
                                        and x.get('originalStartLead') == x.get('leadingExtraSamples'))
        return x['pictureOk'] and x['markedCount'] == 0 and bool(sound)
    starts_ok = all(start_ok(x) for x in result['starts'])
    return dict(markedPicture=s['picture']['markedCount'] == 0, markedSound=s['sound']['exact'] and s['tone']['markedToneMs'] == 0,
                keptPicture=s['picture']['ok'], keptSound=s['sound']['exact'], starts=starts_ok,
                seeks=result.get('seek', {}).get('ok', False),
                exact=s['picture']['ok'] and s['picture']['markedCount'] == 0 and s['sound']['exact']
                and s['tone']['markedToneMs'] == 0 and starts_ok and result.get('seek', {}).get('ok', False))
