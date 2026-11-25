"""Does an AAC track make the decoder draw random noise? (lab detector; never real movies)

Why it matters: FFmpeg's AAC decoder fills perceptual-noise-substitution (PNS) bands from one pseudo-random
generator (`ac->random_state`, an LCG seeded once when the decoder opens, never reset by a flush; FFmpeg 7.1/8.0
`libavcodec/aac/aacdec.c` and `aacdec_proc_template.c`). Its state depends on every noise band decoded before, so
after any seek or cut-list join the noise differs from an uninterrupted decode. A file whose bands never use PNS
draws nothing and decodes the same after a join.

Method, decoder-only: decode the track's own AAC-LC frames twice with the same FFmpeg decoder, once as they are and
once behind a short prefix that is known to draw noise (white noise encoded with PNS on), and compare the float
output from the third frame of the track on (the prefix only changes the first frame's overlap). The two decodes can
differ there only through the generator's state, so any difference means the track draws noise somewhere after its
second frame. A track whose prefix cannot be matched (not AAC-LC, not ADTS-convertible) is "unknown", never "free".
Costs two full decodes of the sound track.
"""
import json
from pathlib import Path

from hard_fixture_lab import media

FRAME = 1024
LAYOUT = {1: 'mono', 2: 'stereo', 6: '5.1'}


def scan(directory, file, stream=0):
    """Return dict(noise=True|False|None, why=...) for audio stream STREAM of DIRECTORY/FILE."""
    directory = Path(directory)
    info = json.loads(media(directory, 'ffprobe', '-select_streams', f'a:{stream}', '-show_streams', '-of', 'json',
                            f'/lab/{file}', readonly=True))['streams'][0]
    if info.get('codec_name') != 'aac' or info.get('profile') != 'LC':
        return dict(noise=None, why=f"not AAC-LC ({info.get('codec_name')} {info.get('profile')})")
    rate, channels = int(info['sample_rate']), int(info['channels'])
    if channels not in LAYOUT:
        return dict(noise=None, why=f'unsupported channel count {channels}')
    stem = f'.noise-{Path(file).stem}-{stream}'
    track, prefix, both = (directory/f'{stem}{s}.aac' for s in ('-track', '-prefix', '-both'))
    try:
        media(directory, 'ffmpeg', '-y', '-i', f'/lab/{file}', '-map', f'0:a:{stream}', '-c', 'copy', '-f', 'adts',
              f'/lab/{track.name}')
        # Independent white noise on every channel (an upmixed mono source leaves 5.1 channels without noise bands).
        noise = '|'.join(f'0.3*(2*random({i})-1)' for i in range(channels))
        media(directory, 'ffmpeg', '-y', '-f', 'lavfi', '-i', f"aevalsrc='{noise}':s={rate}:c={LAYOUT[channels]}:d=0.2",
              '-c:a', 'aac', '-profile:a', 'aac_low',
              '-aac_pns', '1', '-b:a', f'{24*channels}k', '-f', 'adts', f'/lab/{prefix.name}')
        def decode(name):
            return media(directory, 'ffmpeg', '-i', f'/lab/{name}', '-map', '0:a:0', '-f', 'f32le', '-c:a', 'pcm_f32le',
                         'pipe:1', readonly=True)
        # Positive control: the prefix decoded twice in a row must differ the second time, or it draws no noise.
        both.write_bytes(prefix.read_bytes()*2)
        pre, twice = decode(prefix.name), decode(both.name)
        both.write_bytes(prefix.read_bytes() + track.read_bytes())
        alone, behind = decode(track.name), decode(both.name)
    finally:
        for f in (track, prefix, both):
            f.unlink(missing_ok=True)
    size = 4*channels
    if len(twice) != 2*len(pre) or twice[len(pre)+2*FRAME*size:] == pre[2*FRAME*size:]:
        return dict(noise=None, why='positive control failed: the prefix did not move the noise generator')
    if len(behind) != len(pre) + len(alone):
        return dict(noise=None, why='prefix changed the decoded length (decoder reconfigured)')
    behind = behind[len(pre):]
    skip = 2*FRAME*size
    frames = (len(alone) - skip)//(FRAME*size)
    if frames <= 0:
        return dict(noise=None, why='track too short')
    if alone[skip:] == behind[skip:]:
        return dict(noise=False, framesCompared=frames, why='identical after a noise-drawing prefix')
    first = next(i for i in range(skip, len(alone), size) if alone[i:i+size] != behind[i:i+size])
    return dict(noise=True, framesCompared=frames, firstNoiseFrame=first//(FRAME*size),
                why='decoder noise state changes the output')
