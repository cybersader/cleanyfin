"""Shared local HLS and timeline helpers for the hard-fixture prototype."""
from fractions import Fraction
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
LAB = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(LAB))
from hard_fixture_lab import FPS, HZ, media  # noqa: E402


def source_to_filtered(position, cut_start, cut_end):
    """Map positions in [cut_start, cut_end] to the single filtered splice."""
    return position - min(max(position - cut_start, 0), cut_end - cut_start)


def filtered_to_source(position, cut_start, cut_end):
    """Right-continuous inverse: the splice lands at the first post-cut instant."""
    return position if position < cut_start else position + (cut_end - cut_start)


def seconds(value):
    return f'{float(value):.6f}'


def probe(work, name, *args):
    return json.loads(media(work, 'ffprobe', '-protocol_whitelist', 'file',
                            *args, '-of', 'json', f'/lab/{name}', readonly=True))


def ffmpeg(work, *args, **kwargs):
    return media(work, 'ffmpeg', '-xerror', '-protocol_whitelist', 'file,pipe', *args, **kwargs)


def first_pts(work, playlist, selector):
    stream, = probe(work, playlist, '-select_streams', selector, '-show_streams',
                    '-show_entries', 'stream=start_pts,time_base')['streams']
    return stream['start_pts'] * Fraction(stream['time_base'])


def seek_options(work, playlist, filtered_position):
    # HLS in MPEG-TS has a nonzero timestamp origin, and AAC encoder priming
    # can begin before the first picture. Seek relative to the filtered picture.
    return ('-seek_timestamp', '1', '-ss', seconds(first_pts(work, playlist, 'v:0') + filtered_position))


def playlist_parts(path):
    """Parse the small, generated VOD subset; never accept a remote URI."""
    lines = path.read_text().splitlines()
    if (not lines or lines[0] != '#EXTM3U' or lines[-1] != '#EXT-X-ENDLIST'
            or '#EXT-X-PLAYLIST-TYPE:VOD' not in lines
            or '#EXT-X-INDEPENDENT-SEGMENTS' not in lines):
        raise ValueError(f'not an independent-segment VOD playlist: {path.name}')
    init, duration, parts = None, None, []
    for line in lines[1:]:
        if line.startswith('#EXT-X-MAP:'):
            match = re.fullmatch(r'#EXT-X-MAP:URI="([\w.-]+\.mp4)"', line, re.ASCII)
            if not match or init is not None:
                raise ValueError('unsupported init section')
            init = match[1]
        elif line.startswith('#EXTINF:'):
            if duration is not None:
                raise ValueError('missing media URI')
            duration = Fraction(line.removeprefix('#EXTINF:').split(',', 1)[0])
        elif line and not line.startswith('#'):
            if (duration is None or duration <= 0
                    or not re.fullmatch(r'[\w.-]+\.(?:ts|m4s)', line, re.ASCII)):
                raise ValueError('unsupported media URI or duration')
            parts.append((line, duration))
            duration = None
    if (duration is not None or not parts or len({name for name, _ in parts}) != len(parts)
            or any(not name.endswith('.m4s' if init else '.ts') for name, _ in parts)):
        raise ValueError('unexpected VOD segment layout')
    return init, parts
