"""Synthetic copies of the hard fixture in common household stream layouts (lab only, never real movies).

Every layout keeps the hard fixture's 500 barcoded 1280x720 25 fps pictures, its GOP of 50 with B-frames and its
single cut of source frames [137, 263) (5.480 to 10.520 s). The cut sits inside a GOP and inside an audio packet of
every audio track; generation refuses a layout where it does not. Sound is the same 440/880/660 Hz tone equation as
the hard fixture (880 Hz only inside the cut), on every channel at a different level, so a marked sample, a lost
sample or a swapped channel is visible. The exact verdict never trusts the tone: it compares each channel sample by
sample with mpv's own uninterrupted decode of the same file, minus exactly the marked sample indices.
"""
import hashlib
import importlib.util
import json
from fractions import Fraction
from pathlib import Path

from hard_fixture_lab import FPS, HZ, media

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('hard_fixture_script', HERE/'hard-fixture.py')
_hard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hard)
FRAMES, GOP, START, END = _hard.FRAMES, _hard.GOP, _hard.START, _hard.END
START_SAMPLE, END_SAMPLE, SOURCE_SAMPLES = START*HZ//FPS, END*HZ//FPS, FRAMES*HZ//FPS

# Channel order is mpv's/ffmpeg's for each layout. Levels differ so a swapped or dropped channel shows.
CHANNELS = {2: ('stereo', (0.25, 0.20)), 6: ('5.1(side)', (0.25, 0.20, 0.15, 0.25, 0.10, 0.12))}
# (encoder, options, samples per coded frame); the frame size is checked against the muxed packets.
AUDIO = {
    'aac': ('aac', ['-profile:a', 'aac_low', '-b:a', '192k']),
    # Diagnostic: the same encoder with perceptual noise substitution off (the decoder then draws no random noise).
    'aacnopns': ('aac', ['-profile:a', 'aac_low', '-b:a', '192k', '-aac_pns', '0']),
    # Fraunhofer FDK AAC-LC (scene rules name it beside qaac and Nero). It uses noise substitution only at 28-48 kbit/s
    # per channel, so 192k stereo draws no noise and 96k stereo does. The pinned image's FDK has no SBR/PS, so HE-AAC
    # cannot be generated here.
    'fdkaac': ('libfdk_aac', ['-profile:a', 'aac_low', '-b:a', '192k']),
    'fdkaaclow': ('libfdk_aac', ['-profile:a', 'aac_low', '-b:a', '96k']),
    'ac3': ('ac3', ['-b:a', '448k']),
    'eac3': ('eac3', ['-b:a', '640k']),
    'flac': ('flac', ['-sample_fmt', 's16']),
    # 60 ms frames: 20 ms Opus frames would put both cut boundaries exactly on packet edges.
    'opus': ('libopus', ['-b:a', '128k', '-frame_duration', '60']),
}
# ffprobe codec name of each variant above.
CODEC_NAME = {'aacnopns': 'aac', 'fdkaac': 'aac', 'fdkaaclow': 'aac'}
HDR10 = ('hdr10=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:'
         'master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400')
VIDEO = {
    'h264': ['-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p', '-profile:v', 'high',
             '-g', str(GOP), '-bf', '3', '-sc_threshold', '0', '-x264-params', 'b-adapt=0:open-gop=0'],
    'hevc10': ['-c:v', 'libx265', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p10le', '-profile:v', 'main10',
               '-x265-params', f'keyint={GOP}:min-keyint={GOP}:bframes=3:b-adapt=0:scenecut=0:open-gop=0:log-level=error'],
}
VIDEO['hevc10-hdr10'] = VIDEO['hevc10'][:-1] + [VIDEO['hevc10'][-1] + ':' + HDR10, '-color_primaries', 'bt2020',
                                                '-color_trc', 'smpte2084', '-colorspace', 'bt2020nc']

# One subtitle track, flagged default, with events before, across, inside and after the cut (SubRip, ms clock).
SUBTITLES = [(1000, 2000, 'KEPT ONE'), (5000, 6000, 'SPANS CUT START'), (7000, 8000, 'MARKED LINE'),
             (10000, 11000, 'SPANS CUT END'), (15000, 16000, 'KEPT TWO')]


def layout(container, video, audio, subtitles=False):
    """audio: tuple of (codec, channels); the first track is the default one."""
    name = f'{container}-{video}-' + '+'.join(f'{c}{"" if n == 2 else "51"}' for c, n in audio)
    return name + ('-srt' if subtitles else ''), dict(container=container, video=video, audio=tuple(audio), subtitles=subtitles)


LAYOUTS = dict([
    layout('mp4', 'h264', [('aac', 2)]),
    layout('mkv', 'h264', [('aac', 2)]),
    layout('mkv', 'h264', [('ac3', 6)]),
    layout('mkv', 'h264', [('eac3', 6)]),
    layout('mkv', 'hevc10', [('aac', 2)]),
    layout('mkv', 'hevc10', [('eac3', 6)]),
    layout('mkv', 'hevc10-hdr10', [('eac3', 6)]),
    layout('mp4', 'hevc10', [('aac', 2)]),
    layout('mp4', 'h264', [('eac3', 6)]),
    layout('mkv', 'h264', [('flac', 2)]),
    layout('mkv', 'h264', [('opus', 2)]),
    layout('mkv', 'h264', [('eac3', 6), ('aac', 2)], subtitles=True),  # typical: 5.1 default, stereo second, subtitles
    layout('mp4', 'h264', [('aacnopns', 2)]),  # diagnostic only, never admitted
    # Real AAC encoders (R20): FFmpeg native 5.1, FDK LC with and without noise substitution, PNS-free AAC in MKV.
    layout('mp4', 'h264', [('aac', 6)]),
    layout('mp4', 'h264', [('fdkaac', 2)]),
    layout('mp4', 'h264', [('fdkaaclow', 2)]),
    layout('mp4', 'h264', [('fdkaac', 6)]),
    layout('mkv', 'h264', [('aacnopns', 2)]),
    layout('mkv', 'h264', [('fdkaac', 2)]),
])


def tone(level):
    # n is the integer sample index, so neither transition depends on a float timestamp.
    return (f"{level}*sin(2*PI*if(lt(n,{START_SAMPLE}),440*n,"
            f"if(lt(n,{END_SAMPLE}),880*(n-{START_SAMPLE}),660*(n-{END_SAMPLE})))/{HZ})")


def srt():
    def t(ms):
        return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    return ''.join(f'{i}\n{t(a)} --> {t(b)}\n{text}\n\n' for i, (a, b, text) in enumerate(SUBTITLES, 1))


def probe(out, name, *args):
    return json.loads(media(out, 'ffprobe', *args, '-of', 'json', f'/lab/{name}', readonly=True))


def generate(out, name):
    """Write NAME.mkv or NAME.mp4 into the existing directory OUT and return its facts (incl. SHA-256)."""
    spec = LAYOUTS[name]
    file = f"{name}.{spec['container']}"
    inputs = ['-f', 'lavfi', '-i', _hard.barcode_filter()]
    maps = ['-map', '0:v:0']
    codecs = list(VIDEO[spec['video']])
    for i, (codec, channels) in enumerate(spec['audio']):
        layout_name, levels = CHANNELS[channels]
        exprs = '|'.join(tone(level) for level in levels)
        inputs += ['-f', 'lavfi', '-i', f"aevalsrc='{exprs}':s={HZ}:c={layout_name}:d={FRAMES//FPS}"]
        maps += ['-map', f'{i+1}:a:0']
        encoder, options = AUDIO[codec]
        codecs += [f'-c:a:{i}', encoder] + [o if not o.startswith('-') else o.removesuffix(':a') + f':a:{i}' for o in options]
        codecs += [f'-disposition:a:{i}', 'default' if i == 0 else '0']
    if spec['subtitles']:
        (out/f'{name}.srt').write_text(srt())
        inputs += ['-i', f'/lab/{name}.srt']
        maps += ['-map', f'{len(spec["audio"])+1}:s:0']
        codecs += ['-c:s', 'srt', '-disposition:s:0', 'default']
    extra = ['-movflags', '+faststart', '-strict', '-2'] if spec['container'] == 'mp4' else []
    try:
        media(out, 'ffmpeg', *inputs, *maps, *codecs, '-threads:v', '4', *extra, f'/lab/{file}')
    finally:
        (out/f'{name}.srt').unlink(missing_ok=True)
    return check(out, file, spec)


def check(out, file, spec):
    video = probe(out, file, '-select_streams', 'v:0', '-show_streams', '-show_frames', '-show_entries',
                  'stream=codec_name,profile,pix_fmt,time_base:frame=pts,key_frame,pict_type')
    frames, stream = video['frames'], video['streams'][0]
    base = Fraction(stream['time_base'])
    keys = [i for i, f in enumerate(frames) if f['key_frame']]
    if (len(frames) != FRAMES or keys != list(range(0, FRAMES, GOP)) or not any(f['pict_type'] == 'B' for f in frames)
            or any((f['pts']-frames[0]['pts'])*base != Fraction(i, FPS) for i, f in enumerate(frames))):
        raise ValueError(f'{file}: unexpected video layout ({len(frames)} frames, keys {keys[:12]})')
    facts = {'file': file, 'video': {k: stream.get(k) for k in ('codec_name', 'profile', 'pix_fmt', 'time_base')}, 'audio': []}
    for i, (codec, channels) in enumerate(spec['audio']):
        audio = probe(out, file, '-select_streams', f'a:{i}', '-show_streams', '-show_packets', '-show_entries',
                      'stream=codec_name,profile,sample_rate,channels,time_base,start_pts:packet=pts,duration')
        a, packets = audio['streams'][0], audio['packets']
        tb = Fraction(a['time_base'])
        packets = [p for p in packets if 'duration' in p and 'pts' in p]
        inside = all(any(p['pts']*tb < Fraction(b, HZ) < (p['pts']+p['duration'])*tb for p in packets)
                     for b in (START_SAMPLE, END_SAMPLE))
        if a['codec_name'] != CODEC_NAME.get(codec, codec) or int(a['sample_rate']) != HZ or a['channels'] != channels or not inside:
            raise ValueError(f'{file}: audio {i} is not {codec} {channels} ch 48 kHz with the cut inside a packet')
        facts['audio'].append({k: a.get(k) for k in ('codec_name', 'profile', 'channels', 'time_base', 'start_pts')}
                              | {'packetSamples': sorted({round(p['duration']*tb*HZ) for p in packets})[-1]})
    facts['sha256'] = hashlib.sha256((out/file).read_bytes()).hexdigest()
    facts['size'] = (out/file).stat().st_size
    return facts


def plan(facts):
    """Lab plan v0 for analyze-capture.py and exact_audio.py (the hard fixture's single cut)."""
    return {'version': 0, 'source': {'sha256': facts['sha256'], 'frames': FRAMES, 'fps': FPS, 'sampleRate': HZ},
            'cuts': [{'startFrame': START, 'endFrame': END, 'startMs': START*1000//FPS, 'endMs': END*1000//FPS}]}
