using System.Text;
using Cleanyfin.Delivery;
using static Delivery.Contract.Tests.FixtureContract;

namespace Delivery.Contract.Tests;

[Trait("Category", "Mpv")]
public class SubtitleTests
{
    [Theory]
    [InlineData("hdmv_pgs_subtitle")]
    [InlineData("pgssub")]
    [InlineData("dvd_subtitle")]
    [InlineData("dvdsub")]
    public void ImageTracksAreNotAnAvOnlyFallback(string codec) => Assert.Null(MpvSubtitleCues.ForEmbeddedTrack("mkv", codec));

    [Fact]
    public void DropsBothTouchingEdgesAndEveryOverlapBeforeRounding()
    {
        var plan = PlanWithCuts(10_000, (2_000, 4_000), (6_000, 7_000));
        var text = Text(MpvSubtitleCues.Render(plan, MpvPartitions.Joint, EmbeddedTextCodec.SubRip, 1, 2000, [],
            [Cue(2000, 1000, "before"), Cue(3500, 500, "left edge"), Cue(3600, 7200, "overlapping two marks"),
             Cue(4400, 1000, "inside"), Cue(8000, 1000, "right edge"), Cue(8001, 1801, "one tick after"),
             Cue(14002, 1998, "last")]));
        Assert.Equal("1\n00:00:01,000 --> 00:00:01,500\nbefore\n\n" +
                     "2\n00:00:02,001 --> 00:00:02,901\none tick after\n\n" +
                     "3\n00:00:04,001 --> 00:00:05,000\nlast\n\n", text);
    }

    [Fact]
    public void MatchesTheActualMkvEdlUsingFileTimestampsRatherThanRawPlanMilliseconds()
    {
        var (bytes, context) = WithCuts(10_020, (2020, 4020));
        var clocks = context.Source.Tracks.Select(t => t with { AnchorPts = t.Kind == "video" ? -256 : -960 }).ToArray();
        var node = System.Text.Json.Nodes.JsonNode.Parse(bytes)!;
        for (var i = 0; i < clocks.Length; i++) node["source"]!["tracks"]![i]!["anchorPts"] = clocks[i].AnchorPts;
        var plan = Require(Parse(Bytes(node), context with { Source = context.Source with { Tracks = [..clocks] } }));
        var text = Text(MpvSubtitleCues.Render(plan, MpvPartitions.SplitPictureAndSound, EmbeddedTextCodec.SubRip, 1, 1000, [],
            [Cue(1000, 1000, "left touch"), Cue(4000, 500, "right touch"), Cue(4001, 999, "kept")]));
        Assert.Equal("1\n00:00:02,001 --> 00:00:03,000\nkept\n\n", text);
    }

    [Fact]
    public void AssKeepsStyleButCannotAddUnexaminedDialogueThroughTheHeader()
    {
        const string header = "[Script Info]\nScriptType: v4.00+\n[V4+ Styles]\n";
        var plan = PlanWithCuts(8000, (2000, 4000));
        var kept = Cue(5000, 1000, "5,2,Default,,0,0,0,,{\\i1}Kept, styled");
        var banned = Cue(3900, 600, "4,1,Default,,0,0,0,,Banned");
        Decision<byte[]> Render(string script) => MpvSubtitleCues.Render(plan, MpvPartitions.Joint, EmbeddedTextCodec.Ass,
            1, 1000, Encoding.UTF8.GetBytes(script), [kept, banned]);
        var text = Text(Render(header));
        Assert.Contains("Dialogue: 2,0:00:03.00,0:00:04.00,Default,,0,0,0,,{\\i1}Kept, styled", text);
        Assert.DoesNotContain("Banned", text);
        Assert.Equal(RefusalCode.UnsupportedSubtitle, Render(header + "[Events]\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Injected").Reason);
    }

    [Fact]
    public void MovTextGapIsNotACueAndUnsupportedPayloadRefuses()
    {
        var plan = PlanWithCuts(8000, (2000, 4000));
        Decision<byte[]> Render(params SubtitlePacket[] packets) => MpvSubtitleCues.Render(plan, MpvPartitions.Joint,
            EmbeddedTextCodec.MovText, 1, 1000, [], packets);
        var ok = Render(new(0, 0, [0, 0]), new(1000, 500, [0, 1, (byte)'A']), new(3900, 500, [0, 1, (byte)'X']));
        Assert.Equal("1\n00:00:01,000 --> 00:00:01,500\nA\n\n", Text(ok));
        Assert.Equal(RefusalCode.UnsupportedSubtitle, Render(new SubtitlePacket(0, 1, [0, 1, 65, 0])).Reason);
        Assert.Equal(RefusalCode.UnsupportedSubtitle, Render(new SubtitlePacket(0, 0, [0, 1, 65])).Reason);
    }

    [Fact]
    public void StripsOriginalSubtitlesEvenWhenEveryCueWasDropped()
    {
        var plan = PlanWithCuts(8000, (2000, 4000));
        var result = MpvSubtitleCues.Render(plan, MpvPartitions.Joint, EmbeddedTextCodec.SubRip, 1, 1000, [],
            [Cue(1000, 1000, "only cue touches")]);
        Assert.Empty(Assert.IsType<byte[]>(result.Value));
        MpvAvTrack[] av = [new("h264", true, false), new("eac3", false, true)];
        var edl = Text(MpvEdlRenderer.Render(plan, Origin, Ids, MpvClockMapping.VerifiedSourceIdentity,
            subtitles: new(av, [], 3)));
        Assert.Contains("!delay_open,media_type=video,codec=h264\n!delay_open,media_type=audio,codec=eac3\n", edl);
        Assert.DoesNotContain("media_type=sub", edl);
        Assert.DoesNotContain("!new_stream", edl);
    }

    private static SubtitlePacket Cue(long pts, long duration, string text) => new(pts, duration, Encoding.UTF8.GetBytes(text));
    private static string Text(Decision<byte[]> decision)
    {
        Assert.True(decision.Accepted, decision.Reason?.ToString());
        return Encoding.UTF8.GetString(Assert.IsType<byte[]>(decision.Value));
    }
}
