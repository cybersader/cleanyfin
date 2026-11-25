"""Exact PCM admission for R21; alignment is diagnostic only, never a repair."""

R21 = 'R21: at most one extra kept sample before a fresh start or resume near the join (21 us at 48 kHz).'


def deviation(kind, count=None, side=None, **detail):
    return dict(kind=kind, **({'count': count} if count is not None else {}),
                **({'side': side} if side is not None else {}), **detail)


def equal_prefix(x, y, size):
    lo, hi = 0, min(len(x), len(y)) // size
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if x[:mid*size] == y[:mid*size]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def classify_pcm(expected, actual, *, size, preceding=b'', marked_before=b'', marked_after=b'', join=None,
                 original_lead=None, fresh=False):
    """Compare on the exact sample clock. Proven preceding kept PCM must be exact and distinguishable from the
    removed side of the same seam; equal values at unrelated source indices are not evidence of a leak.
    The uncut control independently establishes the number of preceding samples at the same picture.
    """
    if any(len(x) % size for x in (expected, actual, preceding, marked_before, marked_after)):
        raise ValueError('incomplete PCM sample frame')
    delta = (len(actual) - len(expected)) // size
    if not delta and expected == actual:
        if original_lead:
            return [deviation('kept-sample-lost', original_lead, 'before-start')], False
        return [], True
    if delta > 0 and actual[delta*size:] == expected:
        prefix = actual[:delta*size]
        kept = len(preceding) >= len(prefix) and preceding.endswith(prefix)
        marked = max((n for n in range(1, min(delta, len(marked_before)//size) + 1)
                      if marked_before.endswith(prefix[-n*size:])), default=0)
        if marked:
            kind = 'audio-identity-ambiguous' if kept else 'marked-sample-presented'
            return [deviation(kind, marked, 'before-start')], False
        if kept:
            extra = delta - (original_lead or 0)
            if extra < 0:
                return [deviation('kept-sample-lost', -extra, 'before-start')], False
            if not extra and original_lead is not None:
                return [], True
            return [deviation('extra-kept-sample', extra, 'before-start', presented=delta,
                              original=original_lead)], fresh and original_lead is not None and extra == 1
        return [deviation('audio-identity-unproven', delta, 'before-start')], False
    if delta < 0 and actual == expected[-delta*size:]:
        return [deviation('kept-sample-lost', -delta, 'at-start')], False

    first = equal_prefix(expected, actual, size)
    if delta and expected[(first + max(-delta, 0))*size:] == actual[(first + max(delta, 0))*size:]:
        if delta < 0:
            return [deviation('kept-sample-lost', -delta, 'at-or-after-start', sample=first)], False
        inserted = actual[first*size:(first + delta)*size]
        if join is not None and first == join and (
                (marked_before and marked_before.endswith(inserted)) or
                (marked_after and marked_after.startswith(inserted))):
            return [deviation('marked-sample-presented', delta, 'join', sample=first)], False
        if first >= delta and expected[(first - delta)*size:first*size] == inserted:
            return [deviation('extra-kept-sample', delta, 'at-or-after-start', sample=first)], False
        return [deviation('audio-identity-unproven', delta, 'at-or-after-start', sample=first)], False

    # Same-length seam substitution can both leak marked PCM and remove a kept sample.
    if not delta and join is not None and first == join:
        for n in range(1, 9):
            end = (first + n)*size
            token = actual[first*size:end]
            if len(token) == n*size and actual[end:] == expected[end:] and (
                    (marked_before and marked_before.endswith(token)) or
                    (marked_after and marked_after.startswith(token))):
                return [deviation('marked-sample-presented', n, 'join', sample=first),
                        deviation('kept-sample-lost', n, 'join', sample=first)], False

    # A reproducible local clock shift is never admitted, even when the shifted values match perfectly.
    x, y = expected[first*size:], actual[first*size:]
    for n in range(1, 9):
        step = n*size
        if min(len(x), len(y)) >= 32*size and (x[step:] == y[:len(x)-step] or y[step:] == x[:len(y)-step]):
            return [deviation('kept-sample-shifted', n, 'at-or-after-start', sample=first)], False
    issues = [deviation('kept-sample-altered', side='at-or-after-start', sample=first)]
    if delta:
        issues.append(deviation('kept-sample-lost' if delta < 0 else 'audio-identity-unproven', abs(delta),
                                'at-or-after-start', sample=first))
    return issues, False
