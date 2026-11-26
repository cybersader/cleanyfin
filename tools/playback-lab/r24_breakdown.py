"""Count *filtered playback takes* in an R24 desktop receipt; never count positive controls as leaks.

A score() exception aborts that position: its entry and subsequent takes have no scores.
The outside-calibration reason identifies exactly one attempted filtered take. Other missing
entries may not have run at all. Paused seek observations are not extra audio/video takes.
"""
import argparse
from fractions import Fraction
import gzip
import json
from pathlib import Path
import re

import format_measure as fm


def take_keys(label):
    m = re.fullmatch(r'\[(\d+), (\d+)\) ms', label)
    if not m: raise ValueError('unknown cut label')
    a,b = map(int, m.groups())
    # landing() uses the relative start and end and the fixture's 500 pictures.
    mark = fm.Mark({'videoAnchorMs': 0, 'durationMs': 20000}, a, b)
    j = mark.join_seconds
    targets = sorted({max(0, j-Fraction(1,1000)), j, j+Fraction(52,100), max(0, j-Fraction(2,10))})
    return ['straight', *(f'start-{float(t):.3f}' for t in targets if mark.landing(t) is not None), 'resume']


def breakdown(positions):
    out = dict(planned=0, scored=0, collisionTakes=0, collisionSamples=0, outsideTakes=0,
               otherUnmeasured=0, markedPictures=0, markedSeekPictures=0, tracedSoundSamples=0,
               residualMatches=0, positiveFreeScored=0, passedPositions=0)
    for pos in positions:
        keys=take_keys(pos['mark']); takes=pos['takes']
        if list(takes) != keys[:len(takes)]: raise ValueError('receipt does not match the take schedule')
        outside=int(pos.get('unmeasured')=='residual outside held-out decoder-history calibration')
        missing=len(keys)-len(takes)
        if outside>missing: raise ValueError('inconsistent calibration stop')
        out['planned']+=len(keys);out['scored']+=len(takes);out['outsideTakes']+=outside
        out['otherUnmeasured']+=missing-outside;out['passedPositions']+=bool(pos['passed'])
        out['markedSeekPictures']+=sum(l['markedCount'] for l in pos.get('seekLandings',[]))
        for t in takes.values():
            out['markedPictures']+=t['markedFrames'];out['tracedSoundSamples']+=t['anchoredMarkedSamples']
            out['residualMatches']+=t['residualMatches'];out['collisionSamples']+=t['exactKeptCollisions']
            out['collisionTakes']+=t['exactKeptCollisions']>0
            out['positiveFreeScored']+=not (t['markedFrames'] or t['markedSoundDetections'])
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('receipt',type=Path);path=parser.parse_args().receipt
    data=path.read_bytes();doc=json.loads(gzip.decompress(data) if path.suffix=='.gz' else data)
    for line,run in doc['runs'].items():
        for layout,entry in run.items():print(line,layout,json.dumps(breakdown(entry['result']['r24Sweep'])))
