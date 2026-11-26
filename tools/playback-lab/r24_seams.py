"""Summarize historical desktop seam observations, never admission or a new build attestation."""
import gzip
import hashlib
import json
from pathlib import Path


def summarize(paths):
    layouts = {}
    for path in sorted(paths):
        receipt = json.loads(gzip.decompress(path.read_bytes()))
        for line, runs in receipt['runs'].items():
            for name, row in runs.items():
                result = row['result']; facts = result.get('tuple')
                if not facts or facts['source']['audioCodec'] not in ('ac3', 'eac3'): continue
                if name in layouts.setdefault(line, {}): raise ValueError(f'duplicate layout {line} {name}')
                takes = [t.get('metrics', {}) for pos in result['sweep'] for t in pos.get('r22Takes', {}).values()]
                def observed(key, select=max):
                    values = [t[key] for t in takes if t.get(key) is not None]
                    return {'measuredTakes': len(values), 'value': select(values) if values else None}
                layouts[line][name] = dict(receipt=path.name, receiptSha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    measuredTuple=facts, plannedPositions=len(receipt['plannedPositions']), recordedTakes=len(takes),
                    extraSilentSamples=observed('extraZeroSamples'), extraQuietSamples=observed('extraQuietSamples'),
                    popMarginRMS=observed('transientMarginRms', min), pictureNotIntactTakes=observed('pictureIntact', lambda v:v.count(False)),
                    worstAvOffsetMs=observed('worstAvOffsetMs'), longestRecoveryMs=observed('recoveryMs'),
                    syncDidNotHoldTakes=observed('staysInSync', lambda v:v.count(False)), keptSoundLostMs=observed('keptSoundLossMs'))
    return dict(purpose='Historical desktop R22/R23 seam observations only. An absent measurement is not zero; this is not an R24 admission.', layouts=layouts)


if __name__ == '__main__':
    base=Path(__file__).resolve().parent/'prototypes/common-formats'
    print(json.dumps(summarize(base.glob('receipt-r2[23]-*.json.gz')), indent=2))
