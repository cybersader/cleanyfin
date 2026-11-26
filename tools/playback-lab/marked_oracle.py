"""Marked-sound oracle for lossy AC-3/E-AC-3 playback: source-position identity per output sample frame.

Question asked of every presented sample frame: which source position does it come from, and is that position
inside the mark? Lossy decoding means a sample's *value* is only reproducible under the same decoder history, so
identity is established in two layers and a third value check:

1. Exact identity. With the source-time code fixture (format_fixtures 'timecode'), every frame of the uncut decode
   is a unique 96-bit key (uniqueness is verified on the decode in hand, never assumed). An output frame equal to
   one of them, all channels bit for bit, *is* that position. This is single-sample resolution with zero noise.
2. Positional identity for altered frames. A decoder restart (join, fresh start, seek) changes up to a few hundred
   samples of history-dependent output. mpv's audio filter packets each come from one decoder run (packets never
   straddle a segment switch), and samples inside one run are contiguous. So every frame of a packet with at least
   one exact frame inherits that packet's lag. A packet with no exact frame belongs to the run of its previous or
   next anchored packet (split only at packet boundaries); the choice is made by residual energy against both
   candidate positions and must be decisive whenever the candidates disagree about markedness. Ambiguity is
   unmeasured, never a pass.
3. Value detector. Any frame that is not exact is also compared with every decoded marked vector; one within
   TAU_LSB (all channels, Euclidean) is a detection even where its position says kept. This catches substitutions
   that keep the packet geometry, as long as their value survived within TAU_LSB.

A marked sample frame is detected when it is exact-marked, positionally marked, or value-marked. The minimum
detectable leak is one sample frame (about 21 us at 48 kHz) wherever layer 1 or 2 resolves the frame; layer 3 bounds
what an altered substitution must look like to be caught. See the marked-detector page for the derivation.
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

TAU_LSB = 64.0          # value-detector radius: far above 1-LSB rounding, far below code separation (certified)
DECISIVE_RATIO = 0.25   # an unanchored packet group needs its best hypothesis at <= 1/4 the energy of the next one
_M1, _M2 = np.uint64(0x9E3779B97F4A7C15), np.uint64(0xC2B2AE3D27D4EB4F)


class Unmeasured(Exception):
    """The oracle could not resolve identity: never a pass."""


def _hash(frames):
    """64-bit mix of a [n, ch] int16 array (ch <= 8); equality is always verified afterwards."""
    v = np.ascontiguousarray(frames, dtype='<i2').view(np.uint16).astype(np.uint64)
    h = np.zeros(len(v), np.uint64)
    with np.errstate(over='ignore'):
        for c in range(v.shape[1]):
            h = (h ^ (v[:, c] << np.uint64((c * 16) % 64))) * _M1
            h ^= h >> np.uint64(29)
        h *= _M2
        h ^= h >> np.uint64(32)
    return h


class SourceCode:
    """The uncut decode as a lookup table: position of every output frame that equals a unique source frame."""

    def __init__(self, uncut, lead):
        self.u = np.ascontiguousarray(uncut, dtype=np.int16)
        self.lead = int(lead)
        if self.u.ndim != 2 or not len(self.u):
            raise Unmeasured('empty uncut decode')
        h = _hash(self.u)
        self.order = np.argsort(h, kind='stable')
        self.sorted = h[self.order]
        _, first, counts = np.unique(self.sorted, return_index=True, return_counts=True)
        # Positions whose frame value (not just hash) occurs more than once cannot carry identity.
        repeated = np.zeros(len(self.u), bool)
        for i in np.flatnonzero(counts > 1):
            idx = self.order[first[i]:first[i] + counts[i]]
            vals = self.u[idx]
            for j, p in enumerate(idx):
                if np.any(np.all(vals == vals[j], axis=1) & (np.arange(len(idx)) != j)):
                    repeated[p] = True
        self.repeated = repeated
        self._trees = {}
        self._full = None

    @property
    def unique_fraction(self):
        return 1.0 - float(self.repeated.mean())

    def locate(self, y):
        """Uncut index of every output frame equal (all channels) to a unique uncut frame, else -1."""
        y = np.ascontiguousarray(y, dtype=np.int16)
        if not len(y):
            return np.zeros(0, np.int64)
        h = _hash(y)
        at = np.searchsorted(self.sorted, h)
        at = np.minimum(at, len(self.sorted) - 1)
        hit = self.sorted[at] == h
        pos = np.where(hit, self.order[at], -1).astype(np.int64)
        ok = pos >= 0
        ok[ok] = np.all(self.u[pos[ok]] == y[ok], axis=1) & ~self.repeated[pos[ok]]
        # Hash collisions with a different frame: search the equal-hash run (rare; exact by construction).
        for i in np.flatnonzero(hit & ~ok):
            lo, hi = np.searchsorted(self.sorted, h[i], 'left'), np.searchsorted(self.sorted, h[i], 'right')
            cand = [p for p in self.order[lo:hi] if np.array_equal(self.u[p], y[i]) and not self.repeated[p]]
            if len(cand) == 1:
                pos[i] = cand[0]
                ok[i] = True
        return np.where(ok, pos, -1)

    def free_lags(self, frames, at, limit=4):
        """Lags (uncut index - output index) suggested by each frame's nearest decoded uncut frame."""
        if self._full is None:
            self._full = cKDTree(self.u.astype(np.float64))
        _, nn = self._full.query(frames.astype(np.float64), k=1)
        values, counts = np.unique(np.asarray(nn) - (at + np.arange(len(frames))), return_counts=True)
        return {int(v) for v in values[np.argsort(-counts)][:limit]}

    def marked_tree(self, lo, hi):
        key = (lo, hi)
        if key not in self._trees:
            self._trees.clear()
            self._trees[key] = cKDTree(self.u[lo:hi].astype(np.float64)) if hi > lo else None
        return self._trees[key]

    def separation(self, lo, hi):
        """Fixture certificate for a mark [lo, hi) in uncut indices: no frame value shared between marked and kept,
        and the smallest Euclidean distance (LSB) from any kept uncut frame to any marked one."""
        tree = self.marked_tree(lo, hi)
        if tree is None:
            return dict(sharedValues=0, minKeptToMarkedLSB=None)
        kept = np.concatenate([self.u[:lo], self.u[hi:]]).astype(np.float64)
        d, _ = tree.query(kept, k=1)
        shared = int(np.count_nonzero(d == 0))
        return dict(sharedValues=shared, minKeptToMarkedLSB=float(d.min()) if len(d) else None)


@dataclass
class Verdict:
    presented: int = 0
    exactKept: int = 0
    exactMarked: int = 0
    displacedExact: int = 0
    alteredKept: int = 0
    positionalMarked: int = 0
    valueMarked: int = 0
    leadIn: int = 0
    duplicates: int = 0
    keptGapSamples: int = 0
    keptLostAtJoins: int = 0
    unanchoredPackets: int = 0
    splitPackets: int = 0
    valueAlignedPackets: int = 0
    decidedPackets: int = 0
    maxAlteredLSB: float = 0.0
    firstPosition: int = -1
    lastPosition: int = -1
    minAlteredToMarkedLSB: float | None = None
    markedPositions: list = field(default_factory=list)

    @property
    def detections(self):
        return self.exactMarked + self.positionalMarked + self.valueMarked

    def as_dict(self):
        d = {k: v for k, v in self.__dict__.items() if k != 'markedPositions'}
        d['markedSoundDetections'] = self.detections
        d['firstMarkedPositions'] = self.markedPositions[:16]
        return d


def _packets_cover(packets, n):
    end = 0
    for _, a, b in packets:
        if a != end or not a < b <= n:
            raise Unmeasured('incomplete AO packet trace')
        end = b
    if end != n or not n:
        raise Unmeasured('incomplete AO coverage')


def assign(y, packets, code, mark_lo=0, mark_hi=0):
    """Source position (uncut index) of every output frame, which frames were exact, and how each unanchored packet
    was resolved. Raises Unmeasured when a packet's run membership decides markedness but is not decisive.

    Anchored packet: every frame takes the lag of the nearest exact frame in the same packet (a run boundary inside
    a packet would show as two lags; it is counted, not hidden). Unanchored packet (no exact frame): candidate lags
    are the previous anchored run, the next anchored run, and the packet's own best value alignment (nearest decoded
    uncut frames). A run boundary can fall at any packet edge: a resume can open with a one-frame packet from the
    run *before* the join (measured: the last kept frame, 2.8 LSB from exact), so a take's first packet is never
    assumed to belong to the run after it."""
    y = np.ascontiguousarray(y, dtype=np.int16)
    _packets_cover(packets, len(y))
    exact = code.locate(y)
    pos = np.full(len(y), -1, np.int64)
    lags = []
    split = 0
    for _, a, b in packets:
        e = exact[a:b]
        idx = np.flatnonzero(e >= 0)
        if not len(idx):
            lags.append(None)
            continue
        lag_at = e[idx] - (a + idx)
        values, counts = np.unique(lag_at, return_counts=True)
        lags.append(int(values[np.argmax(counts)]))
        if len(values) > 1:
            split += 1
        # Nearest exact frame's lag for every frame of the packet.
        j = np.searchsorted(idx, np.arange(b - a))
        left = np.clip(j - 1, 0, len(idx) - 1)
        right = np.clip(j, 0, len(idx) - 1)
        here = np.arange(b - a)
        near = np.where(np.abs(here - idx[left]) <= np.abs(idx[right] - here), left, right)
        pos[a:b] = a + here + lag_at[near]
    if all(l is None for l in lags):
        raise Unmeasured('no exact sample anywhere in the take')
    groups = []
    for i, (_, a, b) in enumerate(packets):
        if lags[i] is not None:
            continue
        prev = next((lags[k] for k in range(i - 1, -1, -1) if lags[k] is not None), None)
        nxt = next((lags[k] for k in range(i + 1, len(packets)) if lags[k] is not None), None)
        cands = {l for l in (prev, nxt) if l is not None} | code.free_lags(y[a:b], a)
        hyps = []
        for lag in cands:
            p = np.arange(a, b) + lag
            inside = (p >= 0) & (p < len(code.u))
            if not inside.any():
                continue
            # Outside the uncut decode (decoder priming before it, or past its end) compares with silence.
            ref = np.zeros((b - a, y.shape[1]))
            ref[inside] = code.u[p[inside]]
            r = float(np.sum((y[a:b].astype(np.float64) - ref) ** 2))
            hyps.append((r, lag, int(np.count_nonzero((p >= mark_lo) & (p < mark_hi)))))
        if not hyps:
            raise Unmeasured('unanchored packet falls outside the source')
        hyps.sort()
        best = hyps[0]
        rivals = [h for h in hyps[1:] if h[2] != best[2]]
        decided = bool(rivals)
        if rivals and not best[0] <= DECISIVE_RATIO * rivals[0][0]:
            raise Unmeasured('altered packet: run membership not decisive where it decides markedness')
        pos[a:b] = np.arange(a, b) + best[1]
        groups.append(dict(start=a, stop=b, packets=1, decided=decided, lag=best[1],
                           fromRun='previous' if best[1] == prev else 'next' if best[1] == nxt else 'value',
                           residuals=[round(h[0], 1) for h in hyps[:3]]))
    if split:
        groups.append(dict(splitPackets=split, packets=0, decided=False))
    return pos, exact, groups


def judge(y, packets, code, mark_lo, mark_hi, tau=TAU_LSB):
    """Count marked sample frames for a mark [mark_lo, mark_hi) in uncut indices. Raises Unmeasured."""
    y = np.ascontiguousarray(y, dtype=np.int16)
    pos, exact, groups = assign(y, packets, code, mark_lo, mark_hi)
    v = Verdict(presented=len(y), unanchoredPackets=sum(g["packets"] for g in groups),
                splitPackets=sum(g.get("splitPackets", 0) for g in groups),
                valueAlignedPackets=sum(g.get("fromRun") == "value" for g in groups),
                decidedPackets=sum(g['decided'] for g in groups))
    is_exact = exact >= 0
    v.displacedExact = int(np.count_nonzero(is_exact & (exact != pos)))
    ident = np.where(is_exact, exact, pos)
    v.firstPosition, v.lastPosition = int(ident[0]), int(ident[-1])
    m = (ident >= mark_lo) & (ident < mark_hi)
    v.exactMarked = int(np.count_nonzero(m & is_exact))
    v.positionalMarked = int(np.count_nonzero(m & ~is_exact))
    v.exactKept = int(np.count_nonzero(~m & is_exact))
    altered = ~is_exact & ~m & (ident >= 0) & (ident < len(code.u))
    v.alteredKept = int(np.count_nonzero(altered))
    v.leadIn = int(np.count_nonzero(ident < code.lead))
    if v.alteredKept:
        dev = np.linalg.norm(y[altered].astype(np.float64) - code.u[ident[altered]], axis=1)
        v.maxAlteredLSB = float(dev.max())
        tree = code.marked_tree(mark_lo, mark_hi)
        if tree is not None:
            d, _ = tree.query(y[altered].astype(np.float64), k=1)
            v.minAlteredToMarkedLSB = float(d.min())
            v.valueMarked = int(np.count_nonzero(d <= tau))
    kept_ident = ident[~m & (ident >= code.lead)]
    if len(kept_ident):
        uniq, counts = np.unique(kept_ident, return_counts=True)
        v.duplicates = int(np.sum(counts - 1))
        for part in (uniq[uniq < mark_lo], uniq[uniq >= mark_hi]):
            if len(part):
                v.keptGapSamples += int(part[-1] - part[0] + 1 - len(part))
    # Kept frames skipped where playback jumps across the mark (reported; R22 kept-sound loss, not marked).
    for s in seams(ident, mark_lo, mark_hi):
        v.keptLostAtJoins += max(0, int(mark_lo - 1 - ident[s])) + max(0, int(ident[s + 1] - mark_hi))
    v.markedPositions = sorted(set(int(p) for p in ident[m]))
    return v


def tau_for(code, mark_lo, mark_hi):
    """Value radius for this mark, fixed by the fixture before any take is read: at most TAU_LSB and at most half
    the smallest distance from any kept uncut frame to a marked one, so an exact kept frame can never be within it."""
    tree = code.marked_tree(mark_lo, mark_hi)
    if tree is None:
        return TAU_LSB, None
    kept = np.concatenate([code.u[:mark_lo], code.u[mark_hi:]]).astype(np.float64)
    d, _ = tree.query(kept, k=1, distance_upper_bound=2*TAU_LSB, workers=4)
    near = d[np.isfinite(d)]
    closest = float(near.min()) if len(near) else None
    if closest is not None and closest == 0:
        raise Unmeasured('fixture shares a frame value between marked and kept positions')
    return (min(TAU_LSB, closest/2) if closest is not None else TAU_LSB), closest


# ---------------------------------------------------------------------------------------------------------------
# Positive challenges: synthetic marked material injected into a real decoded take. Never stored as media; the
# modified arrays exist only in memory for the duration of one judge() call.

KINDS = ('before-join-extend', 'after-join-prefix', 'after-join-prefix-altered', 'join-shift', 'start-early',
         'start-early-altered', 'end-overrun',
         'interior-insert', 'interior-substitute', 'interior-substitute-near')


def _insert(y, packets, at, block, into='left'):
    """Insert BLOCK before output index AT, growing the packet that owns the insertion point."""
    y2 = np.concatenate([y[:at], block, y[at:]])
    k = len(block)
    owners = [i for i, (_, a, b) in enumerate(packets) if ((a < at <= b) if into == 'left' else (a <= at < b))]
    if not owners:
        owners = [i for i, (_, a, b) in enumerate(packets) if a < at <= b]
    owner = owners[0]
    out = []
    for i, (pts, a, b) in enumerate(packets):
        out.append((pts, a + (k if i > owner else 0), b + (k if i >= owner else 0)))
    return y2, out


def _window(y, packets, centre, span=4):
    """Crop to the packets around CENTRE (re-based), so a challenge is judged locally and fast."""
    k = next(i for i, (_, a, b) in enumerate(packets) if a <= centre < b) if centre < len(y) else len(packets) - 1
    lo, hi = max(0, k - span), min(len(packets), k + span + 1)
    a0 = packets[lo][1]
    return y[a0:packets[hi - 1][2]], [(pts, a - a0, b - a0) for pts, a, b in packets[lo:hi]]


def seams(ident, mark_lo, mark_hi):
    """Output indices s where playback jumps from before the mark to after it (last pre-mark frame)."""
    before = ident < mark_lo
    after = ident >= mark_hi
    return [int(i) for i in np.flatnonzero(before[:-1] & after[1:])]


def challenge_set(y, packets, code, mark_lo, mark_hi, kmax=8, interior=6, seed=0, tau=TAU_LSB):
    """Yield (kind, k, site, y', packets') positives for this take; every one must be detected."""
    pos, exact, _ = assign(y, packets, code, mark_lo, mark_hi)
    ident = np.where(exact >= 0, exact, pos)
    rng = np.random.default_rng(seed)
    mark_hi = min(mark_hi, len(code.u))  # source blocks only exist inside the uncut decode
    span = mark_hi - mark_lo
    sites = seams(ident, mark_lo, mark_hi)

    def deviation(at, n, shape):
        d = y[at:at + n].astype(np.int32) - code.u[np.clip(ident[at:at + n], 0, len(code.u) - 1)].astype(np.int32)
        if len(d) < n or not np.any(d):
            d = rng.integers(-300, 301, size=shape)
        return d[:n]

    # A run that starts after the mark (fresh start or resume on or just after the join, or a mark from the file's
    # start) leaks if the decoder run began early: m = gap + k frames earlier, k of them marked.
    if not sites and len(ident) and 0 <= ident[0] - mark_hi <= 4096:
        gap = int(ident[0] - mark_hi)
        for k in range(1, min(kmax, span) + 1):
            m = gap + k
            block = code.u[ident[0] - m:ident[0]]
            yield ('start-early', k, 0, *_insert(y, packets, 0, block, 'right'))
            altered = np.clip(block.astype(np.int32) + deviation(0, m, block.shape), -32768, 32767).astype(np.int16)
            yield ('start-early-altered', k, 0, *_insert(y, packets, 0, altered, 'right'))
    # A take that ends at the mark (a mark to the end of the file) leaks if the last run overran.
    if len(ident) and ident[-1] == mark_lo - 1:
        for k in range(1, min(kmax, span) + 1):
            yield ('end-overrun', k, len(y) - 1, *_insert(y, packets, len(y), code.u[mark_lo:mark_lo + k], 'left'))
    for s in sites:
        # A run overrun or early start continues the run it belongs to: when kept frames were already lost at the
        # join (measured: a subtitle-layout run starting 128 frames after the mark), the leak needs that gap plus k.
        gap_a = int(mark_lo - 1 - ident[s])
        gap_b = int(ident[s + 1] - mark_hi)
        for k in range(1, min(kmax, span) + 1):
            if 0 <= gap_a <= 4096 and mark_lo + k <= len(code.u):
                block = code.u[ident[s] + 1:mark_lo + k]
                yield ('before-join-extend', k, s, *_insert(y, packets, s + 1, block, 'left'))
            if 0 <= gap_b <= 4096 and mark_hi - k >= 0:
                block = code.u[mark_hi - k:ident[s + 1]]
                yield ('after-join-prefix', k, s, *_insert(y, packets, s + 1, block, 'right'))
                # As a restarted decoder would present it: altered by the take's own post-join deviation, or by an
                # MKV-sized deviation (several hundred LSB) when this take's join decoded exactly.
                dev = deviation(s + 1, len(block), block.shape)
                altered = np.clip(block.astype(np.int32) + dev, -32768, 32767).astype(np.int16)
                yield ('after-join-prefix-altered', k, s, *_insert(y, packets, s + 1, altered, 'right'))
            if s >= 0 and s + 1 + k <= len(y) and mark_lo + k <= mark_hi:
                y2 = y.copy()
                y2[s + 1:s + 1 + k] = code.u[mark_lo:mark_lo + k]
                yield ('join-shift', k, s, y2, list(packets))
    kept_idx = np.flatnonzero((exact >= 0) & ((ident < mark_lo) | (ident >= mark_hi)))
    if not len(kept_idx) or span < 1:
        return
    for _ in range(interior):
        for k in (1, 2, 4, 8):
            if k > span or len(kept_idx) < 2:
                continue
            r = int(kept_idx[rng.integers(0, len(kept_idx) - 1)])
            q = int(rng.integers(mark_lo, mark_hi - k + 1))
            block = code.u[q:q + k]
            yield ('interior-insert', k, r, *_insert(y, packets, r, block, 'right'))
            if r + k <= len(y):
                y2 = y.copy()
                y2[r:r + k] = block
                yield ('interior-substitute', k, r, y2, list(packets))
                step = max(1, int(tau // (2*np.sqrt(block.shape[1]))))  # norm <= tau/2
                near = np.clip(block.astype(np.int32) + rng.integers(-step, step + 1, size=block.shape), -32768, 32767)
                y3 = y.copy()
                y3[r:r + k] = near.astype(np.int16)
                yield ('interior-substitute-near', k, r, y3, list(packets))


def run_challenges(y, packets, code, mark_lo, mark_hi, tau, **kw):
    """Judge every positive locally. Returns counts per kind; a 'missed' positive is a detector failure."""
    out = {}
    for kind, k, site, y2, p2 in challenge_set(y, packets, code, mark_lo, mark_hi, tau=tau, **kw):
        row = out.setdefault(kind, dict(planned=0, detected=0, refused=0, missed=0, exactCount=0, missedK=[]))
        row['planned'] += 1
        wy, wp = _window(y2, p2, max(site, 0))
        try:
            v = judge(wy, wp, code, mark_lo, mark_hi, tau)
        except Unmeasured:
            row['refused'] += 1
            continue
        if v.detections:
            row['detected'] += 1
            row['exactCount'] += v.detections == k
        else:
            row['missed'] += 1
            row['missedK'].append(k)
    return out


def ideal_cut(code, mark_lo, mark_hi, start=None, packet=1536):
    """The uncut decode with exactly the marked frames removed, packetized like a decoder (synthetic control)."""
    lo = code.lead if start is None else start
    y = np.concatenate([code.u[lo:max(lo, mark_lo)], code.u[max(lo, mark_hi):]])
    cuts = sorted({0, len(y), max(0, mark_lo - lo), *range(0, len(y), packet)})
    packets = [(0, a, b) for a, b in zip(cuts, cuts[1:]) if b > a]
    return y, packets


