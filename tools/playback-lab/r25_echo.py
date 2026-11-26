"""Marked-correlated decoder-restart delta for the 48 kHz Dolby timecode fixtures.

The uninterrupted decode is the counterfactual with decoder history. Subtract it
at the same proven kept positions. In a long AC-3 transform the lost overlap
contains an in-place term and a time-reversed alias from the same 256-sample
half-block (ATSC A/52, §7.9). The half-block grid is on the fixture source clock:
FFmpeg writes 256 priming samples and 1536 samples per packet. Locate rather than
search for the mirror, and fit it on independent pseudorandom full-band channels
only; the clock tone and LFE cannot identify a reflection. This is a projection,
not an audibility model, and does not credit the R25 exception on a null fit.
"""
from dataclasses import dataclass
import math

import numpy as np

VERSION = 'r25-mdct-projection-v1'
BLOCK = 256
CODE_CHANNELS = (1, 2, 4, 5)
ABSOLUTE_DBFS = -90.0
MASKING_DB = -60.0

# AC-3's alpha-5 KBD window. The forgotten history contributes the reverse
# taper's square to the in-place term and the product to the reflected term.
_k = np.kaiser(BLOCK + 1, math.pi * 5)
_WINDOW = np.sqrt(np.cumsum(_k[:-1]) / _k.sum())
SELF_WEIGHT = _WINDOW[::-1] ** 2
ALIAS_WEIGHT = _WINDOW * _WINDOW[::-1]


def db(amplitude):
    """Represent exact zero as JSON null, not a nonstandard -Infinity."""
    return 20 * math.log10(amplitude) if amplitude > 0 else None


def levels(echo, kept):
    """Per-channel peak and RMS on the SAME samples and with 1.0 full scale."""
    echo, kept = np.asarray(echo), np.asarray(kept)
    if echo.shape != kept.shape or echo.ndim != 1 or not len(echo):
        raise ValueError('echo and kept need an equal, nonempty sample window')
    peak = lambda x: float(np.max(np.abs(x)))
    rms = lambda x: float(np.sqrt(np.mean(x ** 2)))
    out = {}
    passes = True
    for key, fn in (('peak', peak), ('rms', rms)):
        e, k = fn(echo), fn(kept)
        out.update({key + 'Dbfs': db(e), 'kept' + key.capitalize() + 'Dbfs': db(k),
                    key + 'RelativeDb': db(e / k) if k else None})
        passes &= e <= 10 ** (ABSOLUTE_DBFS / 20) and e <= k * 10 ** (MASKING_DB / 20)
    return out | {'belowLimits': bool(passes)}


@dataclass
class Estimate:
    positions: np.ndarray
    echo: np.ndarray
    kept: np.ndarray
    model: list

    def rows(self):
        return [dict(channel=c, **m, **levels(self.echo[:, i], self.kept[:, i]))
                for i, (c, m) in enumerate(zip(CODE_CHANNELS, self.model, strict=True))]


def project(source, positions, difference, kept, mark_lo, mark_hi):
    """Project one altered kept half-block on its marked-source code reflection.

    positions and marks are in ORIGINAL input sample indices (no decoder lead).
    difference and kept are s16-scale arrays, not int16 arithmetic. Return None
    for a physical zero-support window: its mirror does not touch the mark.
    Keep the fixed transform basis; do not maximize over lags or over samples.
    """
    p = np.asarray(positions, dtype=np.int64)
    source = np.asarray(source)
    difference, kept = np.asarray(difference), np.asarray(kept)
    if (source.ndim != 2 or source.shape[1] != 6 or difference.shape != (len(p), 6)
            or kept.shape != difference.shape):
        raise ValueError('the projection needs aligned six-channel samples')
    if not len(p):
        return None
    if p.min() < mark_hi or p[-1] >= len(source) or p[0] // BLOCK != p[-1] // BLOCK or (np.diff(p) <= 0).any():
        raise ValueError('expected one ordered kept half-block after the mark')
    local = p % BLOCK
    mirror = 2 * (p - local) + BLOCK - 1 - p
    marked = (mirror >= mark_lo) & (mirror < mark_hi)
    if not marked.any():
        return None
    # Report exactly the support of the marked reflection, without diluting its
    # RMS with the other seconds of kept audio. Distinguish local kept attenuation
    # and kept reflection in the least-squares fit (omitting zero regressors).
    echo, model = [], []
    for c in CODE_CHANNELS:
        self_term = SELF_WEIGHT[local] * source[p, c]
        alias = ALIAS_WEIGHT[local] * source[mirror, c]
        target = alias * marked
        nuisance = np.stack((self_term, alias * ~marked), axis=1)
        nonzero = np.any(nuisance != 0, axis=0)
        X = np.column_stack((target, nuisance[:, nonzero]))
        y = difference[:, c].astype(float) / 32768
        beta, _, rank, _ = np.linalg.lstsq(X, y, rcond=None)
        if rank != X.shape[1]:
            raise ValueError('projected code is not identifiable')
        residual = y - X @ beta
        echo.append(beta[0] * target[marked])
        nuisance_fit = X[:, 1:] @ beta[1:]
        conditional = y - nuisance_fit
        den = np.linalg.norm(conditional) * np.linalg.norm(target)
        model.append(dict(gain=float(beta[0]), correlation=float(conditional @ target / den) if den else 0.,
                          fitResidualRmsDbfs=db(float(np.sqrt(np.mean(residual ** 2))))))
    return Estimate(p[marked], np.asarray(echo).T, kept[marked][:, CODE_CHANNELS] / 32768, model)
