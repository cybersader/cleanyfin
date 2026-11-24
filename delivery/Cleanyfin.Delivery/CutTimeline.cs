using System.Collections.Immutable;

namespace Cleanyfin.Delivery;

public readonly record struct KeptRange(long SourceStartMs, long SourceEndMs, long FilteredStartMs)
{
    public long LengthMs => checked(SourceEndMs - SourceStartMs);
    public long FilteredEndMs => checked(FilteredStartMs + LengthMs);
}

public sealed class CutTimeline
{
    internal CutTimeline(long durationMs, ImmutableArray<CutOperation> operations)
    {
        SourceDurationMs = durationMs;
        var kept = ImmutableArray.CreateBuilder<KeptRange>();
        long source = 0, filtered = 0;
        foreach (var operation in operations)
        {
            AddKept(operation.StartMs);
            source = operation.EndMs;
        }
        AddKept(durationMs);
        KeptRanges = kept.ToImmutable();
        FilteredDurationMs = filtered;

        void AddKept(long end)
        {
            if (end <= source)
                return;
            var range = new KeptRange(source, end, filtered);
            kept.Add(range);
            filtered = range.FilteredEndMs;
        }
    }

    public long SourceDurationMs { get; }
    public long FilteredDurationMs { get; }
    public ImmutableArray<KeptRange> KeptRanges { get; }

    public static long MillisecondsToTicks(long milliseconds) => checked(milliseconds * TimeSpan.TicksPerMillisecond);

    /// <summary>Maps cut instants to the next kept splice, or filtered EOF when no suffix remains.</summary>
    public long SourceToFiltered(long sourceMs)
    {
        if (sourceMs < 0 || sourceMs > SourceDurationMs)
            throw new ArgumentOutOfRangeException(nameof(sourceMs));
        int i = FirstEndingAfter(sourceMs, sourceClock: true);
        if (i == KeptRanges.Length)
            return FilteredDurationMs;
        var kept = KeptRanges[i];
        // A cut instant is before the next kept interval, not inside it.
        return sourceMs < kept.SourceStartMs
            ? kept.FilteredStartMs
            : checked(kept.FilteredStartMs + (sourceMs - kept.SourceStartMs));
    }

    /// <summary>At a splice returns the next kept instant. Filtered EOF maps to its final kept endpoint.</summary>
    public long FilteredToSource(long filteredMs)
    {
        if (filteredMs < 0 || filteredMs > FilteredDurationMs)
            throw new ArgumentOutOfRangeException(nameof(filteredMs));
        if (KeptRanges.IsEmpty)
            throw new InvalidOperationException("An empty timeline has no inverse mapping.");
        int i = FirstEndingAfter(filteredMs, sourceClock: false);
        if (i == KeptRanges.Length)
            return KeptRanges[^1].SourceEndMs;
        var kept = KeptRanges[i];
        return checked(kept.SourceStartMs + (filteredMs - kept.FilteredStartMs));
    }

    private int FirstEndingAfter(long instant, bool sourceClock)
    {
        int left = 0, right = KeptRanges.Length;
        while (left < right)
        {
            int mid = left + (right - left) / 2;
            var r = KeptRanges[mid];
            if ((sourceClock ? r.SourceEndMs : r.FilteredEndMs) <= instant)
                left = mid + 1;
            else
                right = mid;
        }
        return left;
    }
}
