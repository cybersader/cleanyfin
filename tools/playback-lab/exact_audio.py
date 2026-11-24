"""Sample-index oracle for decoded stereo s16le; never align or resample captures."""
import hashlib
import math
import struct
import wave

RATE = 48000
FRAME = struct.Struct('<hh')
BYTES_PER_FRAME = FRAME.size


def read_pcm(path):
    with wave.open(str(path), 'rb') as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (RATE, 2, 2):
            raise ValueError(f'{path.name}: expected 48 kHz stereo signed 16-bit WAV')
        pcm = wav.readframes(wav.getnframes())
        if len(pcm) != wav.getnframes()*BYTES_PER_FRAME:
            raise ValueError(f'{path.name}: incomplete PCM')
        return pcm


def kept_pcm(source, ranges):
    """Extract half-open source sample-index ranges; require the complete reference."""
    if len(source) % BYTES_PER_FRAME:
        raise ValueError('incomplete source stereo PCM sample')
    result = []
    last = 0
    for start, end in ranges:
        if not last <= start < end <= len(source)//BYTES_PER_FRAME:
            raise ValueError(f'invalid kept range [{start}, {end}) for {len(source)//BYTES_PER_FRAME} samples')
        result.append(source[start*BYTES_PER_FRAME:end*BYTES_PER_FRAME])
        last = end
    return b''.join(result)


def plan_frames(plan):
    source = plan['source']
    cut, = plan['cuts']
    if source['sampleRate'] != RATE or RATE % source['fps']:
        raise ValueError('lab plan must map whole video frames to whole PCM samples')
    scale = RATE//source['fps']
    return cut['startFrame']*scale, cut['endFrame']*scale, source['frames']*scale


def compare(expected, actual, join=0):
    """Count output sample frames that differ in either channel and report length separately."""
    if len(expected) % BYTES_PER_FRAME or len(actual) % BYTES_PER_FRAME:
        raise ValueError('incomplete stereo PCM sample')
    counts = [0, 0]; peak = [0, 0]; energy = [0, 0]
    runs = []; nruns = 0; last = -2
    if expected != actual:
        for n, ((xl, xr), (yl, yr)) in enumerate(zip(FRAME.iter_unpack(expected), FRAME.iter_unpack(actual))):
            dl, dr = xl-yl, xr-yr
            if not (dl or dr):
                continue
            side = int(n >= join)
            counts[side] += 1
            energy[side] += dl*dl + dr*dr
            peak[side] = max(peak[side], abs(dl), abs(dr))
            if n != last+1:
                nruns += 1
                if nruns <= 16:
                    runs.append([n, n])
            elif nruns <= 16:
                runs[-1][1] = n
            last = n
    delta = (len(actual)-len(expected))//BYTES_PER_FRAME
    return dict(exact=not (delta or nruns), unequalFrames=sum(counts),
                firstUnequal=runs[0][0] if runs else None, lastUnequal=last if runs else None,
                pre=counts[0], post=counts[1], delta=delta,
                expectedFrames=len(expected)//BYTES_PER_FRAME, actualFrames=len(actual)//BYTES_PER_FRAME,
                peakLRmax=peak, rmsOverUnequal=[round(math.sqrt(e/(2*c)), 4) if c else 0 for e, c in zip(energy, counts)],
                runCount=nruns, firstRunsInclusive=runs,
                expectedSHA256=hashlib.sha256(expected).hexdigest(), actualSHA256=hashlib.sha256(actual).hexdigest())


def compare_cut(plan, source, actual):
    start, end, duration = plan_frames(plan)
    # The source presentation ends at the last planned sample, not any AAC padding.
    return compare(kept_pcm(source, [(0, start), (end, duration)]), actual, start)


def summary(result):
    first, last = result['firstUnequal'], result['lastUnequal']
    extent = 'none' if first is None else f'{first}..{last}'
    return (f'{result["actualFrames"]}/{result["expectedFrames"]} stereo samples; '
            f'{result["unequalFrames"]} differing samples (output index range {extent}); '
            f'length delta {result["delta"]:+d}')


def marked_witness(source, actual, source_index):
    """Find a unique, frame-aligned source identity in the actual PCM, if present."""
    begin = source_index*BYTES_PER_FRAME
    token = source[begin:begin+BYTES_PER_FRAME]
    if len(token) != BYTES_PER_FRAME or sum(v == token for v in stereo_samples(source)) != 1:
        raise ValueError(f'source sample {source_index} is not uniquely identifiable')
    return next((i for i, v in enumerate(stereo_samples(actual)) if v == token), None)


def stereo_samples(pcm):
    return (pcm[i:i+BYTES_PER_FRAME] for i in range(0, len(pcm), BYTES_PER_FRAME))
