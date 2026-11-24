using Cleanyfin.Delivery;
using static Delivery.Contract.Tests.FixtureContract;

namespace Delivery.Contract.Tests;

[Trait("Category", "Plan")]
public class TimelineTests
{
    [Fact]
    public void HeadTailAndAdjacentCutsPreserveOnlyPositiveKeptIntervals()
    {
        var timeline = PlanWithCuts(20, (0, 3), (6, 8), (8, 10), (16, 20)).Timeline;
        Assert.Equal(new[] { new KeptRange(3, 6, 0), new KeptRange(10, 16, 3) }, timeline.KeptRanges);
        Assert.Equal(9, timeline.FilteredDurationMs);
        Assert.Equal(0, timeline.SourceToFiltered(0));
        Assert.Equal(3, timeline.FilteredToSource(0));
        Assert.Equal(3, timeline.SourceToFiltered(8));
        Assert.Equal(10, timeline.FilteredToSource(3));
        Assert.Equal(9, timeline.SourceToFiltered(17));
        Assert.Equal(9, timeline.SourceToFiltered(20));
        Assert.Equal(16, timeline.FilteredToSource(9));
        Assert.Throws<ArgumentOutOfRangeException>(() => timeline.SourceToFiltered(-1));
        Assert.Throws<ArgumentOutOfRangeException>(() => timeline.SourceToFiltered(21));
        Assert.Throws<ArgumentOutOfRangeException>(() => timeline.FilteredToSource(-1));
        Assert.Throws<ArgumentOutOfRangeException>(() => timeline.FilteredToSource(10));
    }

    [Fact]
    public void AllCutHasNoKeptRangesOrInverse()
    {
        var timeline = PlanWithCuts(20, (0, 20)).Timeline;
        Assert.Empty(timeline.KeptRanges);
        Assert.Equal(0, timeline.FilteredDurationMs);
        Assert.Equal(0, timeline.SourceToFiltered(7));
        Assert.Throws<InvalidOperationException>(() => timeline.FilteredToSource(0));
    }

    [Theory]
    [InlineData(3, 8, 7, 12)]
    [InlineData(12, 15, 2, 4)]
    public void OverlappingOrReorderedCutsAreRefused(int start, int end, int nextStart, int nextEnd)
    {
        var (bytes, context) = WithCuts(20, (start, end), (nextStart, nextEnd));
        Assert.Equal(RefusalCode.UnsortedOrOverlappingOperations, Parse(bytes, context).Reason);
    }

    [Theory]
    [InlineData(-1, 1)]
    [InlineData(0, 21)]
    [InlineData(9, 9)]
    [InlineData(9, 8)]
    public void OutOfRangeZeroLengthAndBackwardsIntervalsAreRefused(int start, int end)
    {
        var (bytes, context) = WithCuts(20, (start, end));
        Assert.Equal(RefusalCode.InvalidInterval, Parse(bytes, context).Reason);
    }

    [Fact]
    public void MaximumNumberOfCutsKeepsEveryOtherInstant()
    {
        int count = PlanValidator.MaxOperations;
        var cuts = Enumerable.Range(0, count).Select(i => (2L * i + 1, 2L * i + 2)).ToArray();
        var timeline = PlanWithCuts(2L * count + 1, cuts).Timeline;
        Assert.Equal(count + 1, timeline.KeptRanges.Length);
        Assert.Equal(count + 1, timeline.FilteredDurationMs);
        Assert.Equal(2L * count, timeline.FilteredToSource(count));
    }

    [Fact]
    public void KeptInstantsAreAnOrderedBijectionAndCutsPartitionSource()
    {
        var random = new Random(241847);
        for (int sample = 0; sample < 150; sample++)
        {
            int duration = random.Next(1, 128);
            var cuts = new List<(long Start, long End)>();
            for (int point = 0; point < duration;)
            {
                if (random.Next(3) == 0)
                {
                    var end = Math.Min(duration, point + random.Next(1, 8));
                    cuts.Add((point, end));
                    point = end;
                }
                else
                    point++;
            }

            var timeline = PlanWithCuts(duration, cuts.ToArray()).Timeline;
            Assert.Equal(duration, cuts.Sum(r => r.End - r.Start) + timeline.FilteredDurationMs);
            Assert.Equal(timeline.FilteredDurationMs, timeline.KeptRanges.Sum(r => r.LengthMs));
            long filtered = 0;
            for (long source = 0; source < duration; source++)
            {
                bool cut = cuts.Any(r => source >= r.Start && source < r.End);
                bool kept = timeline.KeptRanges.Any(r => source >= r.SourceStartMs && source < r.SourceEndMs);
                Assert.NotEqual(cut, kept);
                Assert.Equal(filtered, timeline.SourceToFiltered(source));
                if (kept)
                    Assert.Equal(source, timeline.FilteredToSource(filtered++));
            }
            Assert.Equal(filtered, timeline.FilteredDurationMs);
            Assert.Equal(filtered, timeline.SourceToFiltered(duration));
        }
    }
}
