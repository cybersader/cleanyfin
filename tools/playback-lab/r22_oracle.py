"""R22 viewer-rule measurements for the synthetic, untimed desktop-mpv output.

Quantities are measured from the joined output and the same layout's uncut decode. Audio block PTSs are
independent of the position in the WAV; dropped sound must not be counted as a permanent clock shift.
"""
import math
import re
import zlib
from functools import cached_property

import numpy as np
from scipy import signal
from scipy.spatial import cKDTree

from exact_audio import RATE

R22 = 'R22: zero marked material; no silence or pop beyond the ideal; intact picture; A/V recovers in 100 ms; at most 40 ms kept-sound loss.'
SILENCE_DBFS = -60
POP_HP_HZ = 2000
POP_WINDOW_MS = 5
RECOVERY_MS = 100
LOSS_MS = 40
CLOCK_RESOLUTION_MS = 1  # Whole-ms layouts; audio PTS rounded to the nearest sample.
MAX_CONTROL_JITTER_MS = 20  # An unreliable control never sets a looser gate.
MAX_CLOCK_SEARCH_MS = 100
FINGERPRINT_FRACTION = .25  # A sample must be <1/4 of the distance from its anchored kept signature to any marked one.
FINGERPRINT_ENERGY_LIMIT = FINGERPRINT_FRACTION ** 2
CRITERIA = ('marked-material', 'added-silence', 'added-pop', 'kept-picture', 'av-sync', 'kept-sound-loss')
THRESHOLDS = dict(silenceDbfs=SILENCE_DBFS, extraSilenceSamples=0, transientHighPassHz=POP_HP_HZ,
                  transientWindowMs=POP_WINDOW_MS,
                  transientAllowance='Per-channel repeat-control noise; startup also compares the same uncut landing and playback mode.',
                  recoveryMs=RECOVERY_MS, clockResolutionMs=CLOCK_RESOLUTION_MS,
                  maxControlJitterMs=MAX_CONTROL_JITTER_MS, keptSoundLossMs=LOSS_MS,
                  markedFingerprintEnergyLimit=FINGERPRINT_ENERGY_LIMIT)


def classify(m):
    """An absent, incomplete or nonfinite measurement never satisfies its criterion."""
    def measured(key):
        v = m.get(key)
        return v if type(v) in (int, float) and math.isfinite(v) else None

    def maximum(key, limit):
        v = measured(key)
        return v is not None and v <= limit

    margin = measured('transientMarginRms')
    zero = m.get('markedFrames') == 0 and (m.get('markedSamples') == 0 and
            (m.get('audioIdentity') == 'exact' or (m.get('audioIdentity') == 'fingerprint' and
             measured('markedFingerprintEnergy') is not None and
             0 <= m['markedFingerprintEnergy'] < FINGERPRINT_ENERGY_LIMIT and
             m.get('fingerprintPositiveControls') is True)))
    # Evaluate silence both literally and at the study's digital floor. Compare excess, not absolute silence.
    return dict(zip(CRITERIA, (zero, maximum('extraZeroSamples', 0) and maximum('extraQuietSamples', 0),
                               margin is not None and margin >= 0, m.get('pictureIntact') is True,
                               maximum('recoveryMs', RECOVERY_MS) and m.get('staysInSync') is True,
                               maximum('lostSamples', LOSS_MS*RATE//1000))))


_AUDIO = re.compile(r'Parsed_ashowinfo_\d+: n:(\d+) pts:(-?\d+) .*? fmt:s16 .*? rate:48000 nb_samples:(\d+) checksum:([0-9A-F]+)')
_VIDEO = re.compile(r'Parsed_showinfo_\d+: n:\s*\d+ pts:\s*(-?\d+) pts_time:')
TRACE_ARGS = ['--af=format=format=s16,lavfi=[ashowinfo]', '--vf=scale=160:90,lavfi=[showinfo]', '--msg-level=all=warn,ffmpeg=v']


def read_clocks(log, pcm, channels, leading_clip=True):
    """Link audio filter packets to the final AO bytes, not decoder read-ahead or paused seek preroll.

    The observing format filter uses the AO's own packed s16 format at the source 48 kHz rate. Adler-32
    verifies all complete packets backwards from EOF. A single first packet can be clipped downstream
    by mpv's first-picture start; even then its trailing byte count and PTS are constrained by the next
    verified packet. Every other checksum or coverage disagreement is unmeasured.
    """
    data = memoryview(pcm)
    width = 2*channels
    blocks = [(int(t), int(n), int(h, 16)) for _, t, n, h in _AUDIO.findall(log)]
    video = [int(t) / 1000 for t in _VIDEO.findall(log)]  # showinfo's mpv video time base is one microsecond.
    stop = len(data) // width
    out = []
    for t, n, h in reversed(blocks):
        begin = stop - n
        if begin < 0:
            if not leading_clip or not out:
                return [], video
            out.append((t-begin, 0, stop))
            stop = 0
            break
        if zlib.adler32(data[begin*width:stop*width], 0) != h:
            return [], video
        out.append((t, begin, stop))
        stop = begin
        if not stop:
            break
    return (out[::-1] if not stop else []), video


def _longest(mask):
    ends = np.flatnonzero(np.diff(mask.astype(np.int8), prepend=0, append=0))
    return int(np.diff(ends)[::2].max(initial=0))


def silence(x):
    """Longest run when *all* selected channels are zero or below the study's -60 dBFS floor."""
    peak = np.max(np.abs(x.astype(np.int32)), axis=1)
    return _longest(peak == 0), _longest(peak < 32768*10**(SILENCE_DBFS/20))


_HP = signal.butter(2, POP_HP_HZ, btype='highpass', fs=RATE, output='sos')


def transient_channels(x, at):
    """Per-channel 5 ms high-pass RMS centred at the join (one-sided at the file boundaries)."""
    half = (POP_WINDOW_MS*RATE//1000)//2
    lo = max(0, min(at - half, len(x) - 2*half))
    hi = min(len(x), lo + 2*half)
    if hi <= lo:
        return None
    begin = max(0, lo - RATE//100)
    filt = signal.sosfilt(_HP, x[begin:hi] / 32768, axis=0)[lo-begin:]
    return np.sqrt(np.mean(filt*filt, axis=0))


def pcm_array(pcm, channels):
    return np.frombuffer(pcm, '<i2').reshape((-1, channels))


def repeat_noise(pcm, repeat, channels):
    """Worst same-clock 5 ms high-pass RMS delta per channel from two uncut captures."""
    if len(pcm) != len(repeat) or not pcm:
        return None
    if pcm == repeat:
        return [0.] * channels
    width = POP_WINDOW_MS*RATE//1000
    if len(pcm)//(channels*2) < width:
        return None
    delta = (pcm_array(pcm, channels).astype(np.float64)-pcm_array(repeat, channels))/32768
    hp = signal.sosfilt(_HP, delta, axis=0)
    integral = np.cumsum(np.insert(hp*hp, 0, 0., axis=0), axis=0)
    return np.sqrt((integral[width:]-integral[:-width]).max(axis=0)/width).tolist()


def compare_transient(actual, ideal, noise, uncut=None):
    """Compare RMS per channel; a quiet channel's excess cannot hide behind a louder one."""
    if actual is None or ideal is None or noise is None:
        return None
    excess = actual-ideal
    allowance = np.array(noise)
    control = {}
    if uncut is not None:
        observed, expected = uncut
        if observed is None or expected is None:
            return None
        delta = np.array(observed)-np.array(expected)
        allowance += delta
        control = dict(uncutActualRms=observed, uncutIdealRms=expected, uncutExcessRms=delta.tolist())
    margins = allowance-excess
    return dict(actualRms=actual.tolist(), idealRms=ideal.tolist(), noiseRms=list(noise),
                excessRms=excess.tolist(), allowedExcessRms=allowance.tolist(),
                worstExcessRms=float(excess.max()), marginRms=float(margins.min()), **control)


class SourceFingerprint:
    """Use the multichannel aperiodic pilot as a decoded source-time signature (no frequency-only null).

    Interior exact anchors constrain one sound block's source index; a mismatching sample is *not* assigned
    that identity unless it also falls inside a disjoint, per-sample kept neighbourhood. The marked control
    is every decoded vector inside this position's mark, not only the fixture's 880 Hz region. When the
    kept and marked vectors coincide or the separation is too small, the fallback cannot clear that sample.
    """
    def __init__(self, pcm, channels, lead, a, b):
        self.pcm, self.width, self.lead = pcm, 2*channels, lead
        self.x = pcm_array(pcm, channels)
        self.a, self.b = lead+a, min(lead+b, len(self.x))

    @cached_property
    def marked(self):
        return cKDTree(self.x[self.a:self.b]) if self.a < self.b else None

    def locate(self, packet, nominations):
        """Find a unique voted start via 2 ms all-channel exact slices; never align by nearest tone phase."""
        w = self.width
        n = len(packet)//w
        if not n:
            return None
        size = min(96, n)
        votes = set()
        for pos in sorted({0, (n-size)//2, n-size}):
            token = packet[pos*w:(pos+size)*w]
            for nominal in nominations:
                radius = MAX_CLOCK_SEARCH_MS*RATE//1000
                lo = max(0, nominal+pos-radius)
                hi = min(len(self.x), nominal+pos+radius+size)
                i = self.pcm.find(token, lo*w, hi*w)
                while i >= 0:
                    if i % w == 0:
                        votes.add(i//w-pos)
                        if len(votes) > 1:
                            return None
                    i = self.pcm.find(token, i+1, hi*w)
        return votes.pop() if votes else None

    def measure(self, y, source_at):
        """Count known marked samples; certify the residual only via the calibrated marked control.

        The energy is squared error in units of the nearest marked-control separation. A clean control has
        energy zero; substituting its nearest marked vector into exactly one sample gives energy one.
        The distance inequality keeps the matched signal in the kept neighbourhood even with decoder
        differences. An unknown sample outside that neighbourhood is not admitted as kept.
        """
        end = source_at + len(y)
        if source_at < 0 or end > len(self.x):
            return None, None, False
        samples_marked = max(0, min(self.b, end) - max(self.a, source_at))
        dx = self.x[source_at:end]
        mismatches = np.any(dx != y, axis=1)
        if not mismatches.any():
            return samples_marked, 0., True
        if not self.marked:
            return samples_marked, None, False
        known, observed = dx[mismatches], y[mismatches]
        distances, ids = self.marked.query(known)
        substituted = self.x[self.a + ids]
        control_distance = np.linalg.norm(known.astype(np.float64)-substituted, axis=1)
        # Explicit one-sample positive control at every mismatch, including short/sub-frame marks.
        if np.any(distances < 1) or np.any(control_distance != distances):
            return samples_marked, None, False
        distance = np.linalg.norm(known.astype(np.float64)-observed, axis=1)
        return samples_marked, float(np.max((distance/distances)**2)), True


def _ms(samples):
    return samples * 1000 / RATE


def control_clock(packets, video, count, lead):
    """Calibrate timestamp jitter/offset against the same uncut A/V capture, not a fixed generous allowance."""
    if not packets or len(video) < count or count < 2:
        return None
    video = np.array(video[-count:])
    video_residual = video - np.arange(count)*40  # The 25 fps source barcode fixture.
    video_bias = float(np.median(video_residual))
    video_jitter = float(np.max(np.abs(video_residual-video_bias)))
    audio_residual = np.array([_ms(pts-begin+lead)-video_bias for pts, begin, _ in packets])
    audio_bias = float(np.median(audio_residual))
    jitter = float(np.max(np.abs(audio_residual-audio_bias))) + video_jitter + CLOCK_RESOLUTION_MS
    if jitter > MAX_CONTROL_JITTER_MS:
        return None
    return dict(videoBiasMs=video_bias, videoJitterMs=video_jitter,
                audioBiasMs=audio_bias, jitterMs=jitter)


def _coverage(ranges, allowed):
    """Union source ranges before counting: extra or repeated samples never offset a drop."""
    sorted_ranges = sorted(ranges)
    runs = []
    for a, b in sorted_ranges:
        if not runs or a > runs[-1][1]:
            runs.append([a, b])
        else:
            runs[-1][1] = max(b, runs[-1][1])
    return sum(b-a-sum(max(0, min(b,d)-max(a,c)) for c,d in runs) for a,b in allowed if a < b)


def observe(pcm, packets, video, seq, *, source, mark, control, ideal, ideal_at, join, picture_ok, exact_issues,
            fresh=False, onset_control=None):
    """Score one complete joint take; a timestamp without PCM identity is not used as mark proof."""
    channels = source.width//2
    actual = pcm_array(pcm, channels)
    reference = pcm_array(ideal, channels)
    m = dict(markedFrames=sum(f in mark.marked_frames for f in seq), markedSamples=0,
             audioIdentity='unproven', pictureIntact=False, packets=len(packets))
    actual_join = None
    # Silence/pop measurements are retained even when a different criterion refuses a take. A
    # nominal join is for diagnosis only; the actual splice must be located for these to pass.
    ideal_join = max(0, min(join, len(reference)))
    if control is None or not packets or not seq or any(f is None for f in seq) or len(video) < len(seq):
        return m
    expected_video = np.array([float(mark.frame_time(f))*1000 for f in seq])
    v = np.array(video[-len(seq):])
    # Use the captured video clock, permitting a shared whole-timeline rebase but not a frame hold.
    video_rebase = float(np.median(v-expected_video))
    video_jitter = float(np.max(np.abs(v-expected_video-video_rebase)))
    m['extraVideoTimingMs'] = video_jitter-control['videoJitterMs']
    m['pictureIntact'] = picture_ok and m['extraVideoTimingMs'] <= CLOCK_RESOLUTION_MS
    elapsed = []
    ranges = []
    marked = 0
    peak_energy = 0.
    measured = 0
    exact = True
    proofs = True
    first_after = None
    join_pts = float(mark.join_seconds)*1000 + video_rebase
    audio_bias = control['audioBiasMs']
    raw = memoryview(pcm)
    for pts, begin, stop in packets:
        nominal = round((pts - (video_rebase+audio_bias)*RATE/1000))
        # Near the splice, audio could be from either side. Both nominations are searched;
        # the waveform, rather than the renderer's asserted timestamp, identifies its source.
        span = mark.b-mark.a
        if abs(nominal-mark.a) <= MAX_CLOCK_SEARCH_MS*RATE//1000:
            sources = (source.lead+nominal, source.lead+nominal+span)
        else:
            sources = (source.lead+nominal+(span if nominal >= mark.a else 0),)
        packet = bytes(raw[begin*source.width:stop*source.width])
        at = source.locate(packet, sources)
        if at is None:
            proofs = False
            continue
        n = stop-begin
        marked_count, energy, calibrated = source.measure(actual[begin:stop], at)
        if marked_count is None:
            proofs = False
            continue
        marked += marked_count
        proofs &= calibrated
        if energy is not None:
            peak_energy = max(peak_energy, energy)
            exact &= energy == 0
        ranges.append((at, at+n))
        measured += n
        if first_after is None and at+n > source.b and at <= source.b + RATE*MAX_CLOCK_SEARCH_MS//1000:
            first_after = begin+max(0, source.b-at)
        # Clock offsets use the source of the content, not the number of samples previously written to WAV.
        relative = at-source.lead
        kept = relative if relative < mark.a else relative-span
        offset = _ms(pts-kept)-video_rebase-audio_bias
        elapsed.append((_ms(pts), _ms(pts+n), offset))
    # R21's explicit seam witnesses are also hard failures even if a block trace disagrees.
    marked = max(marked, sum(d.get('count', 1) for d in exact_issues if d['kind'] == 'marked-sample-presented'))
    m['markedSamples'] = marked
    m['unidentifiedSamples'] = len(actual)-measured
    if measured == len(actual) and proofs and exact:
        m['audioIdentity'] = 'exact'
    elif any(d['kind'] == 'audio-identity-ambiguous' for d in exact_issues):
        m['audioIdentity'] = 'ambiguous'
    if measured == len(actual) and proofs and not exact:
        # Substituting a reference marked vector checks the detector's arithmetic,
        # not its sensitivity to the same marked sound under a different decoder history.
        # Until those positives are captured, the residual is diagnostic and cannot clear the take.
        m.update(markedFingerprintEnergy=peak_energy, fingerprintPositiveControls=False)
    if join <= 0:
        actual_join = first_after if mark.a == 0 and first_after is not None else 0
    elif join >= len(reference):
        actual_join = len(actual)
    elif first_after is not None:
        actual_join = first_after
    if actual_join is not None:
        win = RATE*RECOVERY_MS//1000
        zeros, quiet = silence(actual[max(0, actual_join-win):actual_join+win])
        ideal_zeros, ideal_quiet = silence(reference[max(0, ideal_join-win):ideal_join+win])
        m.update(zeroSamples=zeros, quietSamples=quiet, idealZeroSamples=ideal_zeros, idealQuietSamples=ideal_quiet,
                 extraZeroSamples=zeros-ideal_zeros, extraQuietSamples=quiet-ideal_quiet)
    transients = {}
    noise = control.get('transientNoiseRms')
    if not fresh or 0 < join <= len(reference):
        # Any splice played through must meet the ideal hard cut, even after a fresh start or seek.
        transients['splice'] = (compare_transient(transient_channels(actual, actual_join),
                                                  transient_channels(reference, ideal_join), noise)
                                if actual_join is not None else None)
    if fresh:
        transients['onset'] = (compare_transient(transient_channels(actual, 0), transient_channels(reference, 0),
                                                 noise, onset_control) if onset_control is not None else None)
    m['transientComparisons'] = transients
    if transients and all(t is not None for t in transients.values()):
        m['transientExcessRms'] = max(t['worstExcessRms'] for t in transients.values())
        m['transientMarginRms'] = min(t['marginRms'] for t in transients.values())
    # ideal_at is in the marked-out full PCM. Derive the exact source intervals without counting
    # any incidental sample before a fresh start as owed, or any marked sample toward coverage.
    split = source.a - (source.lead if not mark.a else 0)
    if ideal_at < split:
        allowed = [(ideal_at, split), (source.b, source.b+len(reference)-(split-ideal_at))]
    else:
        after = source.b + ideal_at-split
        allowed = [(after, after+len(reference))]
    uncovered = _coverage(ranges, allowed)
    if m['audioIdentity'] == 'exact':
        m['lostSamples'] = uncovered
        m['keptSoundLossMs'] = _ms(uncovered)
    else:
        # Anchors can establish partial coverage, but cannot assign an unidentified sample.
        m['keptSoundLossLowerBoundMs'] = _ms(max(0, len(reference)-len(actual)))
    if elapsed and measured == len(actual):
        m['worstAvOffsetMs'] = max(abs(x[2]) for x in elapsed)
        m['ordinaryJitterMs'] = control['jitterMs']
        # A fresh start after the join must sync from its own landing. At a terminal
        # cut there is no picture beyond the join, so require sync to the last retained picture.
        start_pts = max(join_pts, float(v[0])) if join < len(reference) else float(v[0])
        # PTS may overlap by a timestamp tick. The recovered suffix must nonetheless
        # have real in-tolerance audio and must not contain unobserved clock intervals.
        after = sorted((a, b, x) for a, b, x in elapsed if b > start_pts)
        limit = control['jitterMs']
        last_bad = max([start_pts] + [b for _, b, x in after if abs(x) > limit])
        good = [(a, b) for a, b, x in after if b > last_bad and abs(x) <= limit]
        if good:
            recovered_at = max(good[0][0], last_bad)
            m['recoveryMs'] = recovered_at - start_pts
            covered = recovered_at
            stays = True
            for a, b in good:
                if a > covered + CLOCK_RESOLUTION_MS:
                    stays = False
                covered = max(covered, b)
            m['staysInSync'] = stays and covered + CLOCK_RESOLUTION_MS >= float(v[-1])
    return m
