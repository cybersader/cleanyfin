"""Joint picture + decoded-sound measurement of one layout through real desktop mpv (lab only).

One playback per take, with numbered frame barcodes, 48 kHz signed 16-bit PCM and media timestamps from the
same playback. R22 scores the output against the uninterrupted decode and the ideal hard cut on the true source
clock. The older sample-exact R21 comparisons are retained only as diagnostics and explicit marked-sample witnesses.

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
from functools import cache
from pathlib import Path

from exact_audio import RATE, compare, kept_pcm, read_pcm, summary
from r21_oracle import classify_pcm, deviation
import r22_oracle as viewer

try:
    from PIL import Image  # Optional fast path for the lab's already-scaled PNGs.
except ImportError:
    Image = None
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
            if b <= a or ((b + z) <= 0 and b != cut['durationMs']):
                continue  # MpvEdlRenderer.FileSegments: nothing presentable before file time 0
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
            result = subprocess.run(cmd, input=(source+'\n').encode(), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=self.timeout)
            if result.returncode:
                raise Unmeasured(f'mpv capture exited {result.returncode}')
            return result.stdout.decode(errors='replace')
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
        self.mpv(source, ['--untimed=yes', '--vo=image', '--vo-image-format=png',
                          '--vo-image-png-compression=1', f'--vo-image-outdir={frames.name}', '--ao=pcm',
                          '--ao-pcm-waveheader=yes', f'--ao-pcm-file={wav.name}', f'--audio-samplerate={RATE}',
                          f'--audio-channels={CHANNEL_LAYOUT[channels]}', '--audio-format=s16', *viewer.TRACE_ARGS,
                          f'--log-file={log.name}', '--script=subs.lua', *extra])
        if not wav.exists() or not any(frames.iterdir()):
            raise Unmeasured(f'{label}: no picture or sound captured')
        text = log.read_text(errors='replace') if log.exists() else ''
        subs = [l.split('SUBTEXT ', 1)[1] for l in text.splitlines() if 'SUBTEXT ' in l]
        tracks = sorted({int(l.rsplit(' ', 1)[1]) for l in text.splitlines() if 'AUDIO-TRACK ' in l})
        clears = [float(l.rsplit(' ', 1)[1]) for l in text.splitlines() if 'SUBCLEAR ' in l]
        sub_tracks = [json.loads(l.split('SUBTRACKS ', 1)[1]) for l in text.splitlines() if 'SUBTRACKS ' in l]
        return frames, wav, dict(subtitles=subs, subtitleClears=clears, subtitleTracks=(sub_tracks or [[]])[-1],
                                 audioTracks=tracks, clockTrace=text)

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


@cache
def _analyzer():
    import importlib.util
    spec = importlib.util.spec_from_file_location('analyze_capture', LAB/'analyze-capture.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_sequence(directory):
    a = _analyzer()
    if Image is not None:
        return [read_one(path) for path in sorted(directory.glob('*.png'))]
    from hard_fixture_lab import SCAN_HEIGHT, SCAN_WIDTH, media
    raw = media(directory, 'ffmpeg', '-protocol_whitelist', 'file,pipe', '-f', 'concat', '-safe', '0', '-i', 'pipe:0',
                '-map', '0:v:0', '-vf', f'scale={SCAN_WIDTH}:{SCAN_HEIGHT}:flags=area,format=gray,settb=1/{FPS},setpts=N',
                '-fps_mode', 'passthrough', '-enc_time_base:v', 'filter', '-threads:v', '1', '-f', 'rawvideo', 'pipe:1',
                readonly=True, data=a.image_manifest(directory))
    size = SCAN_WIDTH*SCAN_HEIGHT
    return [a.read_barcode(memoryview(raw)[o:o+size], FRAMES) for o in range(0, len(raw), size)]


def read_one(image):
    from hard_fixture_lab import SCAN_HEIGHT, SCAN_WIDTH, media
    if Image is not None:
        with Image.open(image) as decoded:
            if decoded.size == (SCAN_WIDTH, SCAN_HEIGHT):
                return _analyzer().read_barcode(memoryview(decoded.convert('L').tobytes()), FRAMES)
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
        ranges = ([(0, lead + self.a)] if self.a else []) + ([(lead + self.b, lead + total)] if self.b < total else [])
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
    packet_clock, picture_clock = viewer.read_clocks(extra.pop('clockTrace'), pcm, channels)
    control = dict(frames=len(seq), unreadable=sum(f is None for f in seq), samples=len(pcm)//(2*channels), **extra)
    if seq != list(range(FRAMES)):
        raise Unmeasured('uncut control did not show all 500 pictures once and in order')
    lead = reference_lead(pcm, channels)
    clock = viewer.control_clock(packet_clock, picture_clock, len(seq), lead)
    if clock is not None:
        noise = None
        try:
            rf, rw, rx = runner.capture('control-repeat', control_source, channels, *mpv_options)
            repeated = read_pcm(rw, channels)
            repeat_clocks = viewer.read_clocks(rx['clockTrace'], repeated, channels)
            if repeat_clocks == (packet_clock, picture_clock) and runner.sequence(rf) == seq:
                noise = viewer.repeat_noise(pcm, repeated, channels)
            shutil.rmtree(rf)
            rw.unlink()
            (runner.work/'control-repeat.log').unlink(missing_ok=True)
        except (Unmeasured, subprocess.TimeoutExpired, ValueError, EOFError):
            pass
        clock['transientNoiseRms'] = noise
    return dict(cut=cut, channels=channels, pcm=pcm, lead=lead, source=control_source, startControls={},
                control=control | dict(lead=lead, r22Clock=clock), r22Clock=clock, fingerprints={})


def start_control(runner, ref, first, mpv_options, resume):
    """Capture an uncut start or seek-resume with the same first source picture, once per mode/picture."""
    controls = ref['startControls']
    key = (resume, first)
    if key not in controls:
        controls[key] = (None, {})
        size, channels = 2*ref['channels'], ref['channels']
        at = (ref['lead'] + first*SAMPLES_PER_FRAME)*size
        original_ideal = ref['pcm'][at:]
        try:
            if resume:
                take = seeks(runner, ref['source'], mpv_options, [(None, 0), (first*FRAME_MS/1000, first)],
                             marked_frames=set(), resume_channels=channels)
                sequence, original, clocks = take['resume']
                if not take['ok']:
                    return controls[key]
            else:
                frames, wav, extra = runner.capture('original-start', ref['source'], channels,
                                                     f'--start={first*FRAME_MS/1000:.6f}', *mpv_options)
                sequence = runner.sequence(frames)
                original = read_pcm(wav, channels)
                clocks = viewer.read_clocks(extra['clockTrace'], original, channels)
                shutil.rmtree(frames)
                wav.unlink()
                (runner.work/'original-start.log').unlink(missing_ok=True)
        except (Unmeasured, subprocess.TimeoutExpired, ValueError, EOFError):
            return controls[key]
        if sequence != list(range(first, FRAMES)) or not clocks[0] or len(clocks[1]) < len(sequence):
            return controls[key]
        # Same-picture calibration must contain audio at the landing, not a delayed or muted control.
        audio_ms = clocks[0][0][0]*1000/RATE
        video_ms = clocks[1][-len(sequence)]
        if abs(audio_ms-video_ms) > viewer.MAX_CONTROL_JITTER_MS:
            return controls[key]
        matches = [j for j in range(9) if at >= j*size and original[:(j + SAMPLES_PER_FRAME)*size] ==
                   ref['pcm'][at - j*size:at + SAMPLES_PER_FRAME*size]]
        actual_pcm, ideal_pcm = viewer.pcm_array(original, channels), viewer.pcm_array(original_ideal, channels)
        comparators = {}
        for omit_first in (False, True):
            observed = viewer.transient_onset_channels(actual_pcm, omit_first)
            ideal = viewer.transient_onset_channels(ideal_pcm, omit_first)
            if observed is not None and ideal is not None:
                comparators[omit_first] = observed.tolist(), ideal.tolist()
        controls[key] = matches[0] if len(matches) == 1 else None, comparators
    return controls[key]


def take(runner, source, ref, mark, label, *extra_args, start=None, mpv_options=()):
    """One playback from START (filtered seconds) or the beginning: picture and sound against the mark."""
    args = ([f'--start={float(start):.6f}'] if start is not None else []) + list(extra_args) + list(mpv_options)
    frames, wav, extra = runner.capture(label, source, ref['channels'], *args)
    seq = runner.sequence(frames)
    got = read_pcm(wav, ref['channels'])
    clocks = viewer.read_clocks(extra.pop('clockTrace'), got, ref['channels'])
    return score_take(runner, ref, mark, label, seq, got, start, mpv_options, clocks) | extra


def score_take(runner, ref, mark, label, seq, got, start, mpv_options, clocks):
    size = 2*ref['channels']
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
    issues = []
    if p['markedCount']:
        issues.append(deviation('marked-frame-presented', p['markedCount']))
    if not p['ok'] and (not p['markedCount'] or p['missing'] or p['unreadable'] or p['duplicates']):
        issues.append(deviation('kept-picture-missing-altered-or-shifted'))
    tail, at = expected, 0
    fresh = start is not None and first in mark.kept_frames
    n, lead, pcm = 0, ref['lead'], ref['pcm']
    if fresh:
        # First shown picture is the start reference. Straight play preserves the whole kept sample sequence,
        # including at the head of a cut; it does not get the fresh-start exception.
        n = first*SAMPLES_PER_FRAME
        at = (lead if mark.a else 0) + mark.kept_index(n)
        tail = expected[at*size:]
    before = pcm[(lead + max(mark.a, mark.b - 8))*size:(lead + mark.b)*size]
    after = pcm[(lead + mark.a)*size:(lead + min(mark.a + 8, mark.b))*size]
    original_lead, onset_control = None, None
    # Only the actual join target earns R23, not a nearby start or a straight cut playing through it.
    onset_omit_first = fresh and start == mark.join_seconds
    if fresh:
        control_lead, controls = start_control(runner, ref, first, mpv_options, resume=label == 'resume')
        onset_control = controls.get(onset_omit_first)
        if control_lead is not None:
            # For the R21 diagnostic, exclude original prefix samples inside the mark.
            original_lead = sum(not mark.a <= i < mark.b for i in range(n - control_lead, n))
    near_join = fresh and -Fraction(1, 5) <= start - mark.join_seconds <= Fraction(13, 25)
    join = (lead if mark.a else 0) + mark.a - at
    pcm_issues, sound_ok = classify_pcm(tail, got, size=size, preceding=expected[max(0, at - 8)*size:at*size],
                                        marked_before=before if join >= 0 else b'',
                                        marked_after=after if join >= 0 else b'', join=join,
                                        original_lead=original_lead, fresh=near_join)
    if fresh and original_lead is None and sound_ok:
        pcm_issues.append(deviation('original-start-unproven'))
        sound_ok = False
    # Full bit comparison is useful for the fixture's original mark; the sweep uses the faster R21 classifier.
    sound = (compare(tail, got, max(0, join), ref['channels']) if label == 'cut' and
             mark.start_frame == START and mark.end_frame == END else
             dict(exact=tail == got, expectedFrames=len(tail)//size, actualFrames=len(got)//size,
                  delta=(len(got)-len(tail))//size))
    sound['summary'] = summary(sound, 'sample frames') if 'firstUnequal' in sound else (
        f"{sound['actualFrames']}/{sound['expectedFrames']} sample frames; length delta {sound['delta']:+d}")
    sound['originalStartLead'] = original_lead
    fingerprints = ref['fingerprints']
    mark_id = (mark.a, mark.b)
    if mark_id not in fingerprints:
        fingerprints.clear()  # One layout+position's nearest-mark index at a time.
        fingerprints[mark_id] = viewer.SourceFingerprint(pcm, ref['channels'], lead, *mark_id)
    observed = viewer.observe(got, *clocks, seq, source=fingerprints[mark_id], mark=mark,
                              control=ref['r22Clock'], ideal=tail, ideal_at=at, join=join,
                              picture_ok=p['ok'], exact_issues=pcm_issues, fresh=start is not None,
                              onset_control=onset_control, onset_omit_first=onset_omit_first,
                              original_lead=original_lead)
    criteria = viewer.classify(observed)
    missing = ['unmeasured-clock'] if not clocks[0] or ref['r22Clock'] is None or 'extraVideoTimingMs' not in observed else []
    if ref['r22Clock'] is not None and ref['r22Clock'].get('transientNoiseRms') is None:
        missing.append('unmeasured-transient-noise')
    if start is not None and onset_control is None:
        missing.append('unmeasured-transient-onset-control')
    r22 = dict(metrics=observed, criteria=criteria, unmeasured=missing,
               passed=all(criteria.values()) and not missing)
    return dict(picture=p, sound=sound, soundOk=sound_ok, exact=p['ok'] and sound_ok,
                deviations=issues + pcm_issues, r22=r22)


def sweep_position(runner, source, ref, mark, *, mpv_options=(), seek=None):
    """The straight cut, fresh starts around the join and paused exact seeks around it, for one mark."""
    j = mark.join_seconds
    result = dict(mark=mark.label(), startFrame=mark.start_frame, endFrame=mark.end_frame, samples=[mark.a, mark.b])
    def attempt(capture):
        try:
            return capture()
        except (Unmeasured, subprocess.TimeoutExpired) as exc:
            return dict(r22=dict(metrics={}, criteria=viewer.classify({}),
                                 unmeasured=[f'unmeasured-{type(exc).__name__}'], passed=False))

    straight = attempt(lambda: take(runner, source, ref, mark, 'cut', mpv_options=mpv_options))
    result['straight'] = straight
    starts = []
    for at in sorted({max(Fraction(0), j - Fraction(1, 1000)), j, j + Fraction(52, 100), max(Fraction(0), j - Fraction(2, 10))}):
        if mark.landing(at) is None:
            continue
        starts.append(dict(startSeconds=float(at), **attempt(
            lambda: take(runner, source, ref, mark, 'start', start=at, mpv_options=mpv_options))))
    result['starts'] = starts
    if seek is not None:
        targets = [(None, mark.kept_frames[0])]
        before_join = j - Fraction(413, 10000)
        resume_at = j if mark.landing(j) is not None else before_join
        for t in (j, j - Fraction(13, 10000), before_join, j + Fraction(10007, 10000), Fraction(1, 2), resume_at):
            if t >= 0 and mark.landing(t) is not None:
                targets.append((float(t), mark.landing(t)))
        seek_result = attempt(lambda: seek(targets))
        result['seek'] = seek_result
        if 'r22' in seek_result:
            result['resume'] = seek_result
        else:
            resume = seek_result.pop('resume', None)
            if resume is not None:
                rseq, rpcm, clocks = resume
                result['resume'] = attempt(lambda: score_take(
                    runner, ref, mark, 'resume', rseq, rpcm, resume_at, mpv_options, clocks))
    takes = [('straight cut', straight)] + [(f"start {s['startSeconds']:.3f} s", s) for s in starts]
    if 'resume' in result:
        takes.append(('resume after seek', result['resume']))
    result['r22Takes'] = {label: t['r22'] for label, t in takes}
    issues = []
    for label, t in result['r22Takes'].items():
        issues.extend(deviation(kind, take=label) for kind, passed in t['criteria'].items() if not passed)
        issues.extend(deviation(kind, take=label) for kind in t['unmeasured'])
    for l in result.get('seek', {}).get('landings', []):
        if l['marked']:
            issues.append(deviation('marked-material', l['markedCount'], take=f"seek {l['target']}"))
        if not seek_picture_ok(l):
            issues.append(deviation('kept-picture', take=f"seek {l['target']}",
                                    expected=l['expected'], landed=l['landed']))
    result['deviations'] = issues
    result['unmeasured'] = (seek is None or 'resume' not in result or
                            any(t['unmeasured'] for t in result['r22Takes'].values()))
    result['passed'] = (not result['unmeasured'] and all(t['passed'] for t in result['r22Takes'].values())
                        and result['seek']['ok'])
    return result


def measure(runner, *, source, control_source, probe, channels, plan_path, mpv_options=(), starts=(5.28, 5.48, 6.0), ref=None):
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


def seeks(runner, source, mpv_options=(), targets=None, switch_sid=None, marked_frames=None, resume_channels=None):
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
    resume_wav = w/'seek-resume.wav'
    resume_wav.unlink(missing_ok=True)
    log = w/'seek-resume.log'
    log.unlink(missing_ok=True)
    ao = ['--ao=null', '--vf=scale=160:90'] if resume_channels is None else [
        '--untimed=yes', '--ao=pcm', '--ao-pcm-waveheader=yes', f'--ao-pcm-file={resume_wav.name}',
        f'--audio-samplerate={RATE}', f'--audio-channels={CHANNEL_LAYOUT[resume_channels]}', '--audio-format=s16',
        *viewer.TRACE_ARGS, f'--log-file={log.name}']
    cid = subprocess.check_output(['docker', 'run', '-d', '--pull=never', '--network', runner.network,
        '--user', f'{os.getuid()}:{os.getgid()}', '-e', 'HOME=/tmp', '-v', f'{w}:/lab:z', *runner.mounts, '-w', '/lab', '--entrypoint=mpv',
        MPV, '--no-config', '--load-scripts=no', '--sub-auto=no', '--audio-file-auto=no', '--pause', '--idle', '--keep-open',
        '--hwdec=no', '--framedrop=no', '--hr-seek=yes', '--no-input-terminal', '--really-quiet', '--vo=image',
        '--vo-image-format=png', '--vo-image-png-compression=1', '--vo-image-outdir=seek-images', *ao,
        '--input-ipc-server=/lab/seek.sock', *mpv_options], text=True, timeout=60).strip()
    landings = []
    resumed = {}
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
                presented = [read_one(f) for f in fresh]
                marked_count = sum(f in marked_set for f in presented)
                landings.append(dict(target=target, expected=expected, landed=presented[-1] if presented else None,
                                     marked=bool(marked_count), markedCount=marked_count, presented=presented,
                                     unreadable=any(f is None for f in presented), subtitle=get('sub-text'), sid=get('sid')))
                if target is None and switch_sid is not None:
                    request = send(['set_property', 'sid', switch_sid])
                    if wait_for(lambda val: val.get('request_id') == request).get('error') != 'success' or get('sid') != switch_sid:
                        raise Unmeasured('mpv refused the requested in-player subtitle switch')
            if resume_channels is not None:
                if not fresh or target is None:
                    raise Unmeasured('no exact seek landing to resume')
                for old in images.glob('*.png'):
                    if old != fresh[-1]:
                        old.unlink()
                for key, value in [('keep-open', 'no'), ('pause', False)]:
                    request = send(['set_property', key, value])
                    if wait_for(lambda val: val.get('request_id') == request).get('error') != 'success':
                        raise Unmeasured(f'mpv refused seek resume ({key})')
                end = wait_for(lambda val: val.get('event') == 'end-file')
                if end.get('reason') != 'eof' or not resume_wav.exists():
                    raise Unmeasured('resume did not capture through end of file')
                send(['quit'])
                # The IPC connection closes before the PCM output writes its final WAV header.
                exit_code = subprocess.check_output(['docker', 'wait', cid], text=True, timeout=30).strip()
                if exit_code != '0':
                    raise Unmeasured(f'mpv resume exited with status {exit_code}')
                try:
                    pcm = read_pcm(resume_wav, resume_channels)
                except (ValueError, EOFError) as exc:
                    raise Unmeasured('seek resume did not finish writing PCM') from exc
                clocks = viewer.read_clocks(log.read_text(errors='replace'), pcm, resume_channels)
                resumed['resume'] = (runner.sequence(images), pcm, clocks)
    finally:
        subprocess.run(['docker', 'rm', '-f', cid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        sock.unlink(missing_ok=True)
        resume_wav.unlink(missing_ok=True)
        log.unlink(missing_ok=True)
        shutil.rmtree(images, ignore_errors=True)
    return dict(landings=landings, ok=all(seek_picture_ok(l) for l in landings), **resumed)


def seek_picture_ok(landing):
    return landing['presented'] == [landing['expected']] and not landing['marked'] and not landing['unreadable']


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
