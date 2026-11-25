using Cleanyfin.Delivery;
using Jellyfin.Plugin.Cleanyfin.AuthRange;

namespace Delivery.Contract.Tests;

public class GrantTests
{
    private sealed class Clock : TimeProvider
    {
        internal DateTimeOffset Now = DateTimeOffset.UtcNow;
        public override DateTimeOffset GetUtcNow() => Now;
    }

    [Fact]
    public void SeparatePurposeSlotAndMethod()
    {
        var clock = new Clock(); var table = new GrantTable<object>(clock);
        var owner = new object();
        Assert.Null(table.Issue(owner, clock.Now.AddMinutes(1), out var slot, out var a));
        Assert.Null(table.Issue(owner, clock.Now.AddMinutes(1), out var other, out _));
        var b = table.SourceFor(a).ToString("x32");
        var sub = table.SubtitleFor(a).ToString("x32");
        Assert.Equal(3, new[] { a, b, sub }.Distinct().Count());
        foreach (var method in new[] { "GET", "HEAD" })
        {
            Assert.Null(table.Check(slot, a, Leg.List, method, out var found)); Assert.Same(owner, found);
            Assert.Null(table.Check(slot, b, Leg.Source, method, out _));
            Assert.NotNull(table.Check(slot, b, Leg.List, method, out _));
            Assert.NotNull(table.Check(slot, a, Leg.Source, method, out _));
            Assert.NotNull(table.Check(other, b, Leg.Source, method, out _));
            Assert.Null(table.Check(slot, sub, Leg.Subtitle, method, out var subtitleOwner)); Assert.Same(owner, subtitleOwner);
            Assert.NotNull(table.Check(slot, sub, Leg.Source, method, out _));
            Assert.NotNull(table.Check(slot, b, Leg.Subtitle, method, out _));
            Assert.NotNull(table.Check(slot, a, Leg.Subtitle, method, out _));
            Assert.NotNull(table.Check(other, sub, Leg.Subtitle, method, out _));
        }
        Assert.NotNull(table.Check(slot, b, Leg.Source, "POST", out _));
        Assert.NotNull(table.Check(slot, a[..30], Leg.List, "GET", out _));
        table.Revoke(x => x == owner); Assert.NotNull(table.Check(slot, a, Leg.List, "GET", out _));
    }

    [Fact]
    public void CapacityAbsoluteExpiryAndRollback()
    {
        var clock = new Clock(); var table = new GrantTable<object>(clock, 1);
        var expires = clock.Now.AddSeconds(1);
        Assert.Null(table.Issue(this, expires, out var slot, out var a));
        Assert.Equal(RefusalCode.LimitExceeded, table.Issue(this, expires, out _, out _));
        clock.Now = expires;
        Assert.Equal(RefusalCode.Expired, table.Check(slot, a, Leg.List, "GET", out _));
        Assert.Null(table.Issue(this, expires.AddMinutes(1), out slot, out a));
        clock.Now = expires.AddSeconds(-1);
        Assert.Equal(RefusalCode.ClockRollback, table.Check(slot, a, Leg.List, "GET", out _));
        clock.Now = expires;
        Assert.Equal(RefusalCode.InvalidIdentifier, table.Check(slot, a, Leg.List, "GET", out _));
    }
}
