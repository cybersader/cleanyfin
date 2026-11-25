"""Joint picture + decoded-sound measurement of one layout through real desktop mpv (lab only).

One playback per take, with picture and sound selected together as a viewer would play it: numbered images and
48 kHz signed 16-bit PCM in the layout's own channel count. The exact verdict compares every channel of every sample
with the same mpv's uninterrupted decode of the same file, re-indexed to the true source clock, minus exactly the
marked samples; no resampling or tolerance. Pictures are read from the barcode, so every kept frame must appear once
and in order and no marked frame may appear at all.

Clock: a plan's source time zero is the earliest audio/video first timestamp (the edition rule). Sample n of the
source (n counted from the first picture, which is where the fixture's tone equation starts) is the instant
videoAnchorMs + n/48 ms. A mark [startMs, endMs) marks every sample and frame whose instant lies inside it.

The uninterrupted decode is not assumed to start at sample 0: mpv starts sound at the first picture by truncating
(start - audio frame time) * rate, so a container that stores encoder priming in whole nanoseconds (Matroska
CodecDelay: 256/48000 s is 5333333 ns) makes mpv play one priming sample first. The fixture's tone is a function of
the integer sample index, so its phase gives the reference's lead in whole samples (reference_lead); measured 1 for
Matroska AC-3/E-AC-3 and 0 for MP4, and cross-checked bit-exactly against ffmpeg's decode with priming.
"""
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

from exact_audio import RATE, compare, compare_ranges, kept_pcm, read_pcm, summary
from format_fixtures import END, FPS, FRAMES, START, TONE_HZ, TONE_LEVEL

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


PICTURE_LEAD_US, SOUND_LAG_US = 2, 10  # MpvEdlRenderer.PictureLeadMicroseconds, SoundLagMicroseconds(48000)


def edl_body(source, cut, split=False):
    """A Python mirror of MpvEdlRenderer for one mark (local diagnosis only; the plugin's own cut list is what the
    plugin path measures). Times on the file's own clock: source ms + source zero. Split: picture partition with
    interior boundaries PICTURE_LEAD_US early, sound partition with every boundary, the first start included,
    SOUND_LAG_US late; the end of the file never moves."""
    z = cut['zeroMs']

    def part(interior, leading):
        segments = []
        for a, b in ((0, cut['startMs']), (cut['endMs'], cut['durationMs'])):
            if b <= a:
                continue
            start = max(z, 0)*1000 + leading if a == 0 else max((a + z)*1000 + interior, 0)
            end = (b + z)*1000 + (0 if b == cut['durationMs'] else interior)
            segments.append(f'{source},{seconds(start)},{seconds(end - start)}\n')
        return ''.join(segments)
    if split:
        return ('# mpv EDL v0\n!no_chapters\n!delay_open,media_type=video\n' + part(-PICTURE_LEAD_US, 0) +
                '!new_stream\n!delay_open,media_type=audio\n' + part(SOUND_LAG_US, 0))
    return '# mpv EDL v0\n!no_chapters\n' + part(0, 0)


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


def reference_lead(pcm, channels):
    """How many samples mpv's uninterrupted decode plays before source sample 0, from the phase of channel 0's tone.

    The fixture writes channel 0 as TONE_LEVEL*sin(2 pi TONE_HZ n / RATE) of the integer sample index n before the cut,
    so a least-squares fit of that tone over the first seconds gives the reference's offset to the sample. Anything
    that is not a clean whole-sample offset is unmeasured, never rounded."""
    frame = struct.Struct('<' + 'h'*channels)
    x = [v[0] for v in frame.iter_unpack(pcm[:200_000*2*channels])]
    if len(x) < 200_000:
        raise Unmeasured('uncut control too short to locate the source clock')
    w = 2*math.pi*TONE_HZ/RATE
    s = sum(x[i]*math.sin(w*i) for i in range(2000, 200_000))
    c = sum(x[i]*math.cos(w*i) for i in range(2000, 200_000))
    amplitude = 2*math.hypot(s, c)/(200_000-2000)/32767
    offset = math.atan2(c, s)/w  # x[i] ~ sin(w*(i + offset))
    lead = -round(offset)
    if abs(amplitude/TONE_LEVEL - 1) > 0.05 or abs(offset - round(offset)) > 0.05 or abs(lead) > 8:
        raise Unmeasured(f'uncut control is not the fixture tone on a whole-sample clock (offset {offset:.3f}, '
                         f'amplitude {amplitude:.3f})')
    return lead


class Mark:
    """One cut on the source clock, as the frames and samples it marks (instants inside [start, end))."""

    def __init__(self, cut, start_ms, end_ms):
        self.av = cut['videoAnchorMs']
        self.start_ms, self.end_ms, self.duration_ms = start_ms, end_ms, cut['durationMs']
        s, e = start_ms - self.av, end_ms - self.av  # ms after the first picture
        if not 0 <= s < e:
            raise ValueError(f'mark [{start_ms}, {end_ms}) is not after the first picture')
        self.start_frame = min(FRAMES, -(-s//FRAME_MS))
        self.end_frame = min(FRAMES, -(-e//FRAME_MS))
        self.a, self.b = s*SAMPLES_PER_FRAME//FRAME_MS, e*SAMPLES_PER_FRAME//FRAME_MS
        self.kept_frames = list(range(self.start_frame)) + list(range(self.end_frame, FRAMES))
        self.marked_frames = set(range(self.start_frame, self.end_frame))
        self.join_seconds = Fraction(s, 1000)  # where the join sits on the filtered clock

    def label(self):
        return f'[{self.start_ms - self.av}, {self.end_ms - self.av}) ms'

    def frame_time(self, f):
        """A kept frame's time on the filtered clock (seconds, exact)."""
        ms = f*FRAME_MS if f < self.start_frame else (self.start_ms - self.av) + f*FRAME_MS - (self.end_ms - self.av)
        return Fraction(ms, 1000)

    def kept_index(self, n):
        """Output index of kept source sample n in the filtered sound."""
        return n if n < self.a else self.a + n - self.b

    def expected(self, ref):
        """The filtered sound: the uncut decode with exactly the marked samples (on the source clock) removed. It
        keeps the reference's lead-in (what mpv plays before source sample 0 when it starts at the file's start), so
        index lead + kept_index(n) holds kept source sample n."""
        size = 2*ref['channels']
        lead, pcm = ref['lead'], ref['pcm']
        total = min(len(pcm)//size - lead, (self.duration_ms - self.av)*SAMPLES_PER_FRAME//FRAME_MS)
        ranges = [(0, lead + self.a)] + ([(lead + self.b, lead + total)] if self.b < total else [])
        return kept_pcm(pcm, [r for r in ranges if r[1] > r[0]], ref['channels'])

    def landing(self, target):
        """The kept frame a paused exact seek or a fresh start shows: mpv's hr-seek shows the first frame no more than
        5 ms before the target (player/video.c tolerance). None if a frame sits within 1 us of that edge (a tie)."""
        edge = Fraction(target).limit_denominator(1_000_000) - Fraction(5, 1000)
        for f in self.kept_frames:
            t = self.frame_time(f)
            if abs(t - edge) < Fraction(1, 1_000_000):
                return None
            if t >= edge:
                return f
        return None


def picture(seq, mark):
    readable = [f for f in seq if f is not None]
    return dict(frames=len(seq), unreadable=len(seq)-len(readable), markedCount=sum(f in mark.marked_frames for f in readable),
                missing=len(set(mark.kept_frames) - set(readable)), duplicates=len(readable)-len(set(readable)),
                monotonic=all(a < b for a, b in zip(readable, readable[1:])))


def reference(runner, *, control_source, probe, channels, mpv_options=()):
    """mpv's uninterrupted decode of the original, with every picture, and its lead on the source clock."""
    cut = plan_for(probe)
    frames, wav, extra = runner.capture('control', control_source, channels, *mpv_options)
    seq = runner.sequence(frames)
    pcm = read_pcm(wav, channels)
    control = dict(frames=len(seq), unreadable=sum(f is None for f in seq), samples=len(pcm)//(2*channels), **extra)
    if seq != list(range(FRAMES)):
        raise Unmeasured('uncut control did not show all 500 pictures once and in order')
    lead = reference_lead(pcm, channels)
    return dict(cut=cut, channels=channels, pcm=pcm, lead=lead, source=control_source, control=control | dict(lead=lead))


def take(runner, source, ref, mark, label, *extra_args, start=None, mpv_options=()):
    """One playback from START (filtered seconds) or the beginning: picture and sound against the mark."""
    size = 2*ref['channels']
    args = ([f'--start={float(start):.6f}'] if start is not None else []) + list(extra_args) + list(mpv_options)
    frames, wav, extra = runner.capture(label, source, ref['channels'], *args)
    seq = runner.sequence(frames)
    got = read_pcm(wav, ref['channels'])
    expected = mark.expected(ref)
    first = next((f for f in seq if f is not None), None)
    if start is None:
        want_first = mark.kept_frames[0] if mark.kept_frames else None
    else:
        want_first = mark.landing(start)
    p = picture(seq, mark)
    kept_from = mark.kept_frames[mark.kept_frames.index(first):] if first in mark.kept_frames else None
    if start is not None and first in mark.kept_frames and first != want_first:
        # mpv's start may skip the hr-seek tolerance and show the first kept frame at or after the target instead.
        at_or_after = next((f for f in mark.kept_frames if mark.frame_time(f) >= start), None)
        if first == at_or_after:
            want_first = first
    p['ok'] = (first == want_first and kept_from is not None and [f for f in seq if f is not None] == kept_from
               and not p['unreadable'] and not p['markedCount'])
    p['firstFrame'], p['expectedFirstFrame'] = first, want_first
    tail, at = expected, 0
    if first is not None and first in mark.kept_frames and (start is not None or mark.a == 0):
        # Sound starts at the first shown picture's instant. Only a presentation that starts at the file's own start
        # (the straight play of a mark that does not begin at 0) has the uncut decode's lead-in before it.
        n = first*SAMPLES_PER_FRAME
        at = ref['lead'] + mark.kept_index(n)
        tail = expected[at*size:]
    sound = compare(tail, got, mark.a if start is None else 0, ref['channels'])
    sound['playerStartBehaviour'] = False
    if not sound['exact'] and first in mark.kept_frames:
        # Diagnose a near miss by sample identity: extra or missing leading samples, and what the extra ones are.
        k = next((k for k in range(1, 9) if got[k*size:] == tail), None)
        sound['leadingExtraSamples'] = k
        sound['leadingMissingSamples'] = next((k for k in range(1, 9) if got == tail[k*size:]), None)
        if k:
            n = first*SAMPLES_PER_FRAME
            pcm, lead = ref['pcm'], ref['lead']
            # Marked samples are absent from `expected`; check the extra ones against the marked source directly.
            marked = pcm[(lead + mark.a)*size:(lead + mark.b)*size]
            sound['extraMarked'] = any(got[i*size:(i+1)*size] == marked[j*size:(j+1)*size]
                                       for i in range(k) for j in (range(max(0, mark.b - mark.a - k), mark.b - mark.a)
                                                                   if first >= mark.end_frame else ()))
            sound['extraArePreceding'] = at >= k and got[:k*size] == expected[(at - k)*size:at*size]
            # The fixture's tone can make a marked sample equal a kept one (e.g. marks a whole number of 440 Hz
            # periods long); identity then cannot tell them apart, and the take counts as marked.
            sound['extraAmbiguous'] = sound['extraArePreceding'] and sound['extraMarked']
            if start is not None and sound['extraArePreceding'] and not sound['extraMarked']:
                # Is it the player's start behaviour? Start the unfiltered original at the same picture.
                ofr, owav, _ = runner.capture('original-start', ref['source'], ref['channels'],
                                              f'--start={first*FRAME_MS/1000:.6f}', *mpv_options)
                oseq = runner.sequence(ofr)
                original = read_pcm(owav, ref['channels'])
                want = ref['pcm'][(lead + n)*size:(lead + n + SAMPLES_PER_FRAME)*size]
                olead = next((j for j in range(0, 9) if original[j*size:j*size + len(want)] == want), None)
                sound['originalStartLead'] = olead
                sound['originalFirstFrame'] = next((f for f in oseq if f is not None), None)
                sound['playerStartBehaviour'] = olead == k and sound['originalFirstFrame'] == first
    sound['summary'] = summary(sound, 'sample frames')
    # Exact: every kept sample from the first picture on, none missing, none marked. A fresh start that also plays the
    # filtered stream's own preceding kept samples passes only where the unfiltered original started at the same
    # picture plays the same number of preceding samples (mpv's start behaviour, not filtering; never a marked one).
    sound_ok = sound['exact'] or sound['playerStartBehaviour']
    return dict(picture=p, sound=sound, soundOk=sound_ok, exact=p['ok'] and sound_ok, **extra)


def sweep_position(runner, source, ref, mark, *, mpv_options=(), seek=None):
    """The straight cut, fresh starts around the join and paused exact seeks around it, for one mark."""
    j = mark.join_seconds
    result = dict(mark=mark.label(), startFrame=mark.start_frame, endFrame=mark.end_frame, samples=[mark.a, mark.b])
    straight = take(runner, source, ref, mark, 'cut', mpv_options=mpv_options)
    result['straight'] = straight
    starts = []
    for at in sorted({max(Fraction(0), j - Fraction(1, 1000)), j, j + Fraction(52, 100), max(Fraction(0), j - Fraction(2, 10))}):
        if mark.landing(at) is None:
            continue
        starts.append(dict(startSeconds=float(at), **take(runner, source, ref, mark, 'start', start=at, mpv_options=mpv_options)))
    result['starts'] = starts
    if seek is not None:
        targets = [(None, mark.kept_frames[0])]
        for t in (j, j - Fraction(13, 10000), j - Fraction(413, 10000), j + Fraction(10007, 10000), Fraction(1, 2), j):
            if t >= 0 and mark.landing(t) is not None:
                targets.append((float(t), mark.landing(t)))
        result['seek'] = seek(targets)
    deviations = []
    for label, t in [('straight cut', straight)] + [(f"start {s['startSeconds']:.3f} s", s) for s in starts]:
        if not t['picture']['ok']:
            deviations.append(f"{label}: picture first {t['picture']['firstFrame']} (want {t['picture']['expectedFirstFrame']}), "
                              f"{t['picture']['markedCount']} marked, {t['picture']['missing']} missing")
        if not t['soundOk']:
            deviations.append(f"{label}: {t['sound']['summary']}" + (' (a MARKED sample plays)' if t['sound'].get('extraMarked') else '')
                              + (f" ({t['sound']['leadingExtraSamples']} extra leading; preceding kept: "
                                 f"{t['sound'].get('extraArePreceding')}; original at that picture: "
                                 f"{t['sound'].get('originalStartLead')} extra)" if t['sound'].get('leadingExtraSamples') else '')
                              + (f" ({t['sound']['leadingMissingSamples']} missing leading)" if t['sound'].get('leadingMissingSamples') else ''))
    for l in result.get('seek', {}).get('landings', []):
        if l['landed'] != l['expected'] or l['marked']:
            deviations.append(f"seek {l['target']}: landed on frame {l['landed']}, expected {l['expected']}" + (' (MARKED)' if l['marked'] else ''))
    result['deviations'] = deviations
    result['exact'] = not deviations
    return result


def measure(runner, *, source, control_source, probe, channels, plan_path, mpv_options=(), starts=(5.28, 5.48, 7.0), ref=None):
    """Control, straight cut and fresh starts at the hard fixture's own mark (source frames [137, 263)), on the true
    source clock. Returns a result dict; raises Unmeasured if a take did not finish."""
    ref = ref or reference(runner, control_source=control_source, probe=probe, channels=channels, mpv_options=mpv_options)
    cut = ref['cut']
    av = cut['videoAnchorMs']
    mark = Mark(cut, cut['startMs'], cut['endMs'])
    result = dict(cut=cut, channels=channels, mpvOptions=list(mpv_options), control=ref['control'])
    s = take(runner, source, ref, mark, 'cut', mpv_options=mpv_options)
    frames_dir = runner.work/'cut-frames'
    wav = runner.work/'cut.wav'
    t = runner.tones(wav, plan_path)
    p = s['picture']
    result['straight'] = dict(picture=dict(p, ok=p['ok'] and p['frames'] == FRAMES-(END-START)),
                              sound=s['sound'], tone=dict(markedToneMs=t['leakMs'], ok=t['pass']),
                              **{k: v for k, v in s.items() if k not in ('picture', 'sound', 'exact')})
    shutil.rmtree(frames_dir, ignore_errors=True)
    result['starts'] = []
    for at in starts:
        x = take(runner, source, ref, mark, f'start-{at}', start=Fraction(str(at)), mpv_options=mpv_options)
        result['starts'].append(dict(startSeconds=at, firstFrame=x['picture']['firstFrame'], markedCount=x['picture']['markedCount'],
                                     pictureOk=x['picture']['ok'], sound=x['sound'],
                                     leadingExtraSamples=x['sound'].get('leadingExtraSamples'),
                                     extraMarked=bool(x['sound'].get('extraMarked')),
                                     **{k: v for k, v in x.items() if k not in ('picture', 'sound', 'exact')}))
    assert av == mark.av
    return result


def seeks(runner, source, mpv_options=(), targets=None, switch_sid=None, marked_frames=None):
    """Paused desktop mpv with exact landing pictures and active text, optionally after an in-player track switch.
    MARKED_FRAMES: the frames the current mark removes (default: the fixture's own [137, 263))."""
    marked_set = set(range(START, END)) if marked_frames is None else set(marked_frames)
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
                                     marked=bool(fresh) and any((read_one(f) or -1) in marked_set for f in fresh),
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
        # Exact from the start position on: the first picture the player shows for that target, every kept frame
        # after it, and from that picture's instant every kept sample; extra preceding kept samples only where the
        # unfiltered original does the same (take(): player start behaviour). Never a marked or missing sample.
        return x['pictureOk'] and x['markedCount'] == 0 and x.get('soundOk', x['sound']['exact'])
    starts_ok = all(start_ok(x) for x in result['starts'])
    return dict(markedPicture=s['picture']['markedCount'] == 0, markedSound=s['sound']['exact'] and s['tone']['markedToneMs'] == 0,
                keptPicture=s['picture']['ok'], keptSound=s['sound']['exact'], starts=starts_ok,
                seeks=result.get('seek', {}).get('ok', False),
                exact=s['picture']['ok'] and s['picture']['markedCount'] == 0 and s['sound']['exact']
                and s['tone']['markedToneMs'] == 0 and starts_ok and result.get('seek', {}).get('ok', False))
