"""Lab mirror of EvidenceSourceLabel.Describe: the evidence source a plugin build derives for a verified original.

Kept deliberately separate from the C# code so a lab run cross-checks it: the runner computes the expected source
from Jellyfin's own PlaybackInfo JSON and the snapshot's track clocks, and the plugin only offers playback when its
own derivation names the same register row.
"""
MP4 = {'mov', 'mp4', 'm4a', '3gp', '3g2', 'mj2'}
MKV = {'mkv', 'matroska'}


def family(names):
    parts = {n.strip() for n in (names or '').split(',') if n.strip()}
    return 'mp4' if parts and parts <= MP4 else 'mkv' if parts and parts <= MKV else None


def describe(container, streams, tracks, duration_ms):
    """streams: PlaybackInfo MediaStreams; tracks: snapshot binding tracks. Returns (source dict, audio index)."""
    kinds = {'Video': 'video', 'Audio': 'audio', 'Subtitle': 'subtitle'}
    audio = [s for s in streams if s['Type'] == 'Audio']
    video = [s for s in streams if s['Type'] == 'Video']
    defaults = [a for a in audio if a.get('IsDefault')]
    selected = defaults[0] if len(defaults) == 1 else audio[0] if len(audio) == 1 else None
    if len(video) != 1 or selected is None or family(container) is None:
        raise ValueError('layout the plugin refuses to describe')
    parts = []
    for s in sorted(streams, key=lambda s: s['Index']):
        kind = kinds[s['Type']]
        text = f"{s['Index']} {kind} {s['Codec']}"
        if kind == 'subtitle':
            parts.append(text + (' default' if s.get('IsDefault') else ''))
            continue
        clock, = [t for t in tracks if t['index'] == s['Index'] and t['kind'] == kind]
        if s.get('Profile'):
            text += ' ' + s['Profile']
        if kind == 'video':
            rate = f"{round(s['RealFrameRate'], 3):.3f}".rstrip('0').rstrip('.')
            text += f" {s['BitDepth']} bit {s['VideoRangeType']} {s['Width']}x{s['Height']} {rate} fps"
        else:
            text += f" {s['Channels']} ch {s['SampleRate']} Hz"
        num, den = clock['timeBase']
        text += f" tb {num}/{den} at {clock['anchorSourceMs']} ms" + (' plays' if s is selected else '')
        parts.append(text)
    label = ', '.join(parts)
    return dict(container=family(container), videoCodec=video[0]['Codec'], audioCodec=selected['Codec'],
                trackLayout=label, durationMs=duration_ms), selected['Index']
