using System.Globalization;
using System.Text;
using Cleanyfin.Delivery;
using static Delivery.Contract.Tests.FixtureContract;

namespace Delivery.Contract.Tests;

[Trait("Category", "Mpv")]
public class MpvTests
{
    private const string SourceTail = "/Cleanyfin/play/00000000000000000000000000000001/source?t=00000000000000000000000000000002";
    private const MpvClockMapping Identity = MpvClockMapping.VerifiedSourceIdentity;

    [Fact]
    public void GoldenPlanRendersExactEdlAndMapsSevenSeconds()
    {
        var raw = Golden();
        Assert.True(SchemaAccepts(raw));
        var plan = Require(Parse(raw));
        var bytes = RequireBytes(MpvEdlRenderer.Render(plan, Origin, Ids, Identity));
        Assert.Equal(Fixture("hard-fixture.mpv.edl"), bytes);
        Assert.Equal(12040, plan.Timeline.FilteredToSource(7000));
    }

    [Fact]
    public void UsesInvariantDecimalsUnderGermanCultureAndOptionalChapterHeader()
    {
        var culture = CultureInfo.CurrentCulture;
        var uiCulture = CultureInfo.CurrentUICulture;
        try
        {
            CultureInfo.CurrentCulture = CultureInfo.CurrentUICulture = CultureInfo.GetCultureInfo("de-DE");
            var plan = Require(Parse(Golden()));
            var output = RequireBytes(MpvEdlRenderer.Render(plan, Origin, Ids, Identity, noChapters: true));
            var golden = Encoding.UTF8.GetString(Fixture("hard-fixture.mpv.edl"));
            Assert.Equal(golden.Insert(golden.IndexOf('\n') + 1, "!no_chapters\n"), Encoding.UTF8.GetString(output));
        }
        finally
        {
            CultureInfo.CurrentCulture = culture;
            CultureInfo.CurrentUICulture = uiCulture;
        }
    }

    [Fact]
    public void UsesUtf8ByteLengthAndQuotesCommasAndSemicolonsInConfiguredBasePath()
    {
        const string basePath = "https://jellyfin.example/médiathèque,東京;β";
        var plan = PlanWithCuts(20, (0, 3), (6, 8), (8, 10), (16, 20));
        var output = RequireBytes(MpvEdlRenderer.Render(plan, new Uri(basePath + "/"), Ids, Identity));
        var lines = Encoding.UTF8.GetString(output).Split('\n');
        Assert.Equal(["# mpv EDL v0", ""], new[] { lines[0], lines[^1] });
        Assert.Equal(4, lines.Length); // Two positive kept ranges, no degenerate entries for adjacent cuts.
        CheckEntry(lines[1], ",0.003,0.003");
        CheckEntry(lines[2], ",0.010,0.006");

        static void CheckEntry(string entry, string expectedTimes)
        {
            var bytes = Encoding.UTF8.GetBytes(entry);
            Assert.Equal((byte)'%', bytes[0]);
            var secondPercent = Array.IndexOf(bytes, (byte)'%', 1);
            var byteCount = int.Parse(Encoding.ASCII.GetString(bytes, 1, secondPercent - 1), CultureInfo.InvariantCulture);
            var url = Encoding.UTF8.GetString(bytes, secondPercent + 1, byteCount);
            Assert.Equal(basePath + SourceTail, url);
            Assert.True(byteCount > url.Length);
            Assert.Equal(expectedTimes, Encoding.ASCII.GetString(bytes[(secondPercent + 1 + byteCount)..]));
        }
    }

    [Theory]
    [InlineData("file:///tmp/original.mp4")]
    [InlineData("ftp://jellyfin.example")]
    [InlineData("edl://auto")]
    [InlineData("/not-an-origin")]
    [InlineData("https://jellyfin.example/#fragment")]
    [InlineData("https://jellyfin.example/?t=injected")]
    [InlineData("https://jellyfin.example@foreign.example")]
    [InlineData("https://jellyfin.example/\nbase")]
    [InlineData("https://jellyfin.example/base%0A")]
    [InlineData("https://jellyfin.example//foreign.example")]
    [InlineData("https://jellyfin.example/a/../other")]
    [InlineData("https://jellyfin.example/\\foreign.example")]
    [InlineData("https://jellyfin.example/has space")]
    public void RefusesUnsafeOrUntrustedOriginForms(string origin)
    {
        var result = MpvEdlRenderer.Render(Require(Parse(Golden())), new Uri(origin, UriKind.RelativeOrAbsolute), Ids, Identity);
        Assert.Equal(RefusalCode.InvalidOrigin, result.Reason);
        Assert.Null(result.Value);
    }

    [Fact]
    public void BuildsHttpSourceWithOnlyTrustedBasePathAndFixedIdentifiers()
    {
        const string baseOrigin = "http://127.0.0.1:8096/jellyfin";
        var result = RequireBytes(MpvEdlRenderer.Render(Require(Parse(Golden())), new Uri(baseOrigin), Ids, Identity));
        Assert.Contains(baseOrigin + SourceTail, Encoding.UTF8.GetString(result));
    }

    [Fact]
    public void RefusesNoKeptRangesNoCutsUnverifiedClockAndInvalidIdentifiers()
    {
        Assert.Equal(RefusalCode.NoKeptRanges, MpvEdlRenderer.Render(PlanWithCuts(20, (0, 20)), Origin, Ids, Identity).Reason);
        Assert.Equal(RefusalCode.NoCuts, MpvEdlRenderer.Render(PlanWithCuts(20), Origin, Ids, Identity).Reason);
        var plan = Require(Parse(Golden()));
        Assert.Equal(RefusalCode.UnverifiedPlayerClock,
            MpvEdlRenderer.Render(plan, Origin, Ids, MpvClockMapping.Unverified).Reason);
        Assert.Equal(RefusalCode.InvalidIdentifier,
            MpvEdlRenderer.Render(plan, Origin, default, Identity).Reason);
    }

    [Fact]
    public void UnsupportedFeatureCannotReachTheRendererAsAValidatedPlan()
    {
        var result = Parse(Change(root => root["requires"]!.AsArray().Add("mute")));
        Assert.Equal(RefusalCode.UnsupportedFeature, result.Reason);
        Assert.Null(result.Value);
    }

    private static byte[] RequireBytes(Decision<byte[]> result)
    {
        Assert.True(result.Accepted, result.Reason?.ToString());
        return Assert.IsType<byte[]>(result.Value);
    }
}
