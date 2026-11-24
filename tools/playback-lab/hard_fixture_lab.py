"""Shared media runner and barcode layout for the hard playback fixture."""
import os
import random
import struct
import subprocess

# The one table of Jellyfin lab targets. Each row moves together: the server image (by digest,
# version verified inside), the plugin's JellyfinTarget (see the plugin csproj) and the SDK that
# builds it. Add a row only with its own lab run; a new server never inherits another's evidence.
JELLYFIN = {
    '10.11': dict(image='jellyfin/jellyfin@sha256:aefb67e6a7ff1debdd154a78a7bbb780fd0c873d8639210a7f6a2016ad2b35db',
                  server='10.11.11', target='10.11',
                  sdk='mcr.microsoft.com/dotnet/sdk@sha256:1dfdb27e9c2c345cb598925ccf6b13719ecfebea8467bd4745c23a3c108cdf84'),  # 9.0
    # Docker Hub 12.4.20261026-211555 (multi-arch index; amd64 sha256:7da5184c...).
    '12.4': dict(image='jellyfin/jellyfin@sha256:fea863d0cd9ec445985d9c75fc6ed64e38a1d2766cf0c250a33d553f4fc1b771',
                 server='12.4.0', target='12.4',
                 sdk='mcr.microsoft.com/dotnet/sdk@sha256:8c6edfb26c85153f236d26efebd9ad349aec403583f343f201ee67838b23658d'),  # 10.0
}
DEFAULT_JELLYFIN = '10.11'
# Media tools (fixture generation, probes) stay on the default image so fixture bytes are identical
# whichever server is under test.
IMAGE = JELLYFIN[DEFAULT_JELLYFIN]['image']
FPS, HZ = 25, 48000
IDENTIFIABLE_CUT = (6, 11)  # Omit [0.240, 0.440) s; exposes the lossless join rounding.
WIDTH, HEIGHT = 1280, 720
CELL, X, Y = 64, 160, 296
SYNC = (0, 1, 1, 0, 1, 0)
BITS = 9
SCAN_WIDTH, SCAN_HEIGHT = WIDTH//8, HEIGHT//8


def media(work, tool, *args, readonly=False, data=None):
    mount = f'{work}:/lab:{"ro," if readonly else ""}Z'
    command = ['docker', 'run', '--rm', '--network', 'none',
               '--user', f'{os.getuid()}:{os.getgid()}']
    if data is not None:
        command.append('-i')
    command += ['-v', mount, '--entrypoint', f'/usr/lib/jellyfin-ffmpeg/{tool}', IMAGE]
    command += (['-hide_banner', '-loglevel', 'error', '-nostdin'] if tool == 'ffmpeg'
                else ['-v', 'error'])
    return subprocess.run([*command, *args], input=data, stdout=subprocess.PIPE,
                          check=True).stdout


def write_identifiable_movie(work, source_samples):
    """Keep the hard fixture's pictures; encode unique stereo sample identities losslessly."""
    rng = random.Random(3736549)
    raw = bytearray(rng.randbytes(source_samples*4))
    word = struct.Struct('<I')
    reserved = {value for value, in word.iter_unpack(raw)}
    used = set()
    # Preserve the original experiment's sequence except for repeated identities.
    for index, (value,) in enumerate(word.iter_unpack(raw)):
        if value in used:
            while value in reserved:
                value = rng.getrandbits(32)
            word.pack_into(raw, index*4, value)
            reserved.add(value)
        used.add(value)
    input_file = work/'identifiable.raw'
    input_file.write_bytes(raw)
    try:
        media(work, 'ffmpeg', '-i', '/lab/source.mp4', '-f', 's16le', '-ar', str(HZ),
              '-ac', '2', '-i', '/lab/identifiable.raw', '-map', '0:v:0', '-map', '1:a:0',
              '-c:v', 'copy', '-c:a', 'flac', '-strict', '-2', '-movflags', '+faststart',
              '/lab/identifiable.mp4')
    finally:
        input_file.unlink()
    return raw


def write_cut(work, name, video_cut, audio_cut, source_samples, source='source.mp4'):
    """Trim the decoded MP4 by indexes, not timestamps, without concat A/V padding."""
    vs, ve = video_cut
    a_s, a_e = audio_cut
    media(work, 'ffmpeg', '-i', f'/lab/{source}', '-filter_complex',
          f'[0:v]trim=end_frame={vs},setpts=PTS-STARTPTS[v0];'
          f'[0:v]trim=start_frame={ve},setpts=PTS-STARTPTS[v1];'
          f'[v0][v1]concat=n=2:v=1:a=0,setpts=N/({FPS}*TB)[v];'
          f'[0:a]atrim=end_sample={a_s},asetpts=PTS-STARTPTS[a0];'
          f'[0:a]atrim=start_sample={a_e}:end_sample={source_samples},asetpts=PTS-STARTPTS[a1];'
          '[a0][a1]concat=n=2:v=0:a=1,asetpts=N/SR/TB[a]',
          '-map', '[v]', '-map', '[a]', '-fps_mode', 'passthrough',
          '-c:v', 'ffv1', '-threads:v', '4', '-c:a', 'pcm_s16le', f'/lab/{name}')


def ranges(values):
    """Sorted inclusive runs of integer values."""
    runs = []
    for value in sorted(set(values)):
        if runs and value == runs[-1][1] + 1:
            runs[-1][1] = value
        else:
            runs.append([value, value])
    return runs
