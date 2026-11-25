using System.Collections.Immutable;
using System.Text;
using System.Text.RegularExpressions;
using Cleanyfin.Delivery;
using static Delivery.Contract.Tests.FixtureContract;

namespace Delivery.Contract.Tests;

/// <summary>
/// Common household stream layouts: the evidence source a layout is offered under, and the cut list rendered on
/// the file's own clock. Measurements behind each rule: tools/playback-lab/format-matrix.py.
/// </summary>
[Trait("Category", "Formats")]
public class CommonFormatTests
{
    private const MpvClockMapping Identity = MpvClockMapping.VerifiedSourceIdentity;
    private static readonly Regex Label = new(@"\A[A-Za-z0-9](?:[A-Za-z0-9 :.,/_-]*[A-Za-z0-9:.,/_-])?\z");

    private static readonly ImmutableArray<TrackClock> Mp4Clocks =
        [new(0, "video", 1, 12800, 0, 0), new(1, "audio", 1, 48000, 0, 0)];
    // ffmpeg's Matroska muxer: millisecond time base, AAC priming written as a first timestamp of -21 ms.
    private static readonly ImmutableArray<TrackClock> MkvAacClocks =
        [new(0, "video", 1, 1000, 0, 21), new(1, "audio", 1, 1000, -21, 0)];

    private static SourceTrackFacts Video(string codec = "h264", string profile = "High", int depth = 8, string range = "SDR") =>
        new(0, "video", codec, profile, 1280, 720, 25.0, depth, range);
    private static SourceTrackFacts Audio(int index, string codec, int channels, bool isDefault = false, string? profile = null) =>
        new(index, "audio", codec, profile, Channels: channels, SampleRate: 48000, IsDefault: isDefault);

    [Fact]
    public void HardFixtureLayoutHasAStableLabel()
    {
        var d = EvidenceSourceLabel.Describe("mov,mp4,m4a,3gp,3g2,mj2", [Video(), Audio(1, "aac", 2, true, "LC")], Mp4Clocks, 20000);
        Assert.True(d.Accepted);
        Assert.Equal(new EvidenceSource("mp4", "h264", "aac",
            "0 video h264 High 8 bit SDR 1280x720 25 fps tb 1/12800 at 0 ms, 1 audio aac LC 2 ch 48000 Hz tb 1/48000 at 0 ms plays",
            20000), d.Value!.Source);
        Assert.Equal(1, d.Value.AudioIndex);
        Assert.Matches(Label, d.Value.Source.TrackLayout);
    }

    [Fact]
    public void EveryFactThatChangedExactnessChangesTheLabel()
    {
        string L(string container, SourceTrackFacts[] t, ImmutableArray<TrackClock> c) =>
            EvidenceSourceLabel.Describe(container, t, c, 20000).Value!.Source.TrackLayout + "|" + container;
        var baseline = L("mp4", [Video(), Audio(1, "aac", 2)], Mp4Clocks);
        string[] variants =
        [
            L("mkv", [Video(), Audio(1, "aac", 2)], Mp4Clocks),
            L("mp4", [Video("hevc", "Main 10", 10), Audio(1, "aac", 2)], Mp4Clocks),
            L("mp4", [Video("hevc", "Main 10", 10, "HDR10"), Audio(1, "aac", 2)], Mp4Clocks),
            L("mp4", [Video(), Audio(1, "eac3", 6)], Mp4Clocks),
            L("mp4", [Video(), Audio(1, "aac", 6)], Mp4Clocks),
            L("mkv", [Video(), Audio(1, "aac", 2)], MkvAacClocks),
            L("mp4", [Video(), Audio(1, "aac", 2), new(2, "subtitle", "subrip")], Mp4Clocks),
            L("mp4", [Video(), Audio(1, "aac", 2), new(2, "subtitle", "subrip", IsDefault: true)], Mp4Clocks),
        ];
        Assert.Equal(variants.Length + 1, variants.Append(baseline).Distinct().Count());
        Assert.Contains("1 video h264", L("mkv", [Video() with { Index = 1 }, Audio(0, "aac", 2)],
            [new(1, "video", 1, 1000, 0, 0), new(0, "audio", 1, 1000, 0, 0)]));
    }

    [Fact]
    public void TheDefaultSoundTrackIsTheOnePlayed()
    {
        var two = EvidenceSourceLabel.Describe("mkv", [Video(), Audio(1, "aac", 2, true), Audio(2, "ac3", 6)],
            [.. MkvAacClocks, new(2, "audio", 1, 1000, -5, 16)], 20021);
        Assert.Equal(("aac", 1), (two.Value!.Source.AudioCodec, two.Value.AudioIndex));
        Assert.Contains("1 audio aac 2 ch 48000 Hz tb 1/1000 at 0 ms plays, 2 audio ac3 6 ch", two.Value.Source.TrackLayout);
        var second = EvidenceSourceLabel.Describe("mkv", [Video(), Audio(1, "aac", 2), Audio(2, "ac3", 6, true)],
            [.. MkvAacClocks, new(2, "audio", 1, 1000, -5, 16)], 20021);
        Assert.Equal(("ac3", 2), (second.Value!.Source.AudioCodec, second.Value.AudioIndex));
    }

    [Theory]
    [InlineData("webm")]
    [InlineData("avi")]
    [InlineData("mp4,mkv")]
    [InlineData("")]
    public void UnmeasuredContainersAreRefused(string container) =>
        Assert.Equal(RefusalCode.EvidenceTupleUnknown,
            EvidenceSourceLabel.Describe(container, [Video(), Audio(1, "aac", 2)], Mp4Clocks, 20000).Reason);

    [Fact]
    public void AmbiguousOrIncompleteLayoutsAreRefusedNotGuessed()
    {
        var clocks = Mp4Clocks.Add(new(2, "audio", 1, 48000, 0, 0));
        foreach (var tracks in new SourceTrackFacts[][]
        {
            [Video(), Audio(1, "aac", 2), Audio(2, "ac3", 6)],                 // no default sound track
            [Video(), Audio(1, "aac", 2, true), Audio(2, "ac3", 6, true)],     // two defaults
            [Video(), Audio(1, "aac", 2), new(2, "other", "png")],             // cover art, data, attachments
            [Audio(1, "aac", 2)],                                               // no picture
            [Video(), Video() with { Index = 2 }, Audio(1, "aac", 2)],          // two pictures
            [Video() with { BitDepth = null }, Audio(1, "aac", 2)],             // unknown fact
            [Video(), Audio(1, "aac", 2) with { Profile = "LC(x)" }],           // label cannot say it
            [Video(), Audio(1, "aac", 2), Audio(1, "ac3", 6)],                  // duplicate index
        })
            Assert.Equal(RefusalCode.EvidenceTupleUnknown, EvidenceSourceLabel.Describe("mp4", tracks, clocks, 20000).Reason);
        // A track without a verified clock, or a clock the catalogue does not describe.
        Assert.False(EvidenceSourceLabel.Describe("mp4", [Video(), Audio(1, "aac", 2)], [Mp4Clocks[0]], 20000).Accepted);
        Assert.False(EvidenceSourceLabel.Describe("mp4", [Video(), Audio(1, "aac", 2)], clocks, 20000).Accepted);
    }

    [Theory]
    [InlineData("mov,mp4", "mp4", true)]
    [InlineData("mov", "mp4", true)]
    [InlineData("mkv,webm", "mkv", true)]
    [InlineData("matroska", "mkv", true)]
    [InlineData("mkv", "mp4", false)]
    [InlineData("mp4", "mkv", false)]
    [InlineData(null, "mp4", false)]
    public void ProfilesOfferAContainerFamily(string? profile, string family, bool offered) =>
        Assert.Equal(offered, EvidenceSourceLabel.Offers(profile, family));

    [Fact]
    public void FileClockOfSourceZeroComesFromTheVerifiedTracks()
    {
        var plan = Require(Parse(Golden()));
        Assert.Equal(0, MpvEdlRenderer.FileZeroMicroseconds(plan.Source));
        Assert.Equal(-21_000, MpvEdlRenderer.FileZeroMicroseconds(plan.Source with { Tracks = MkvAacClocks }));
        // Tracks that disagree about where source zero is, or a zero that is not a whole microsecond: refuse.
        Assert.Null(MpvEdlRenderer.FileZeroMicroseconds(plan.Source with { Tracks = [MkvAacClocks[0], Mp4Clocks[1]] }));
        Assert.Null(MpvEdlRenderer.FileZeroMicroseconds(plan.Source with { Tracks = [new(0, "video", 1, 48000, 1, 0)] }));
    }

    [Fact]
    public void MatroskaCutListIsWrittenOnTheFilesOwnTimestamps()
    {
        // Source frames [137, 263) of a Matroska file whose sound starts at -21 ms: source [5501, 10541) ms.
        var plan = MkvPlan(20021, 5501, 10541, MkvAacClocks);
        var edl = Text(MpvEdlRenderer.Render(plan, Origin, Ids, Identity, noChapters: true));
        Assert.Equal($"# mpv EDL v0\n!no_chapters\n{Src},0.000,5.480\n{Src},10.520,9.480\n", edl);
    }

    [Fact]
    public void SplitPartitionsMovePictureEarlierAndSoundLaterByAQuarterSample()
    {
        var plan = MkvPlan(20000, 5480, 10520, [new(0, "video", 1, 1000, 0, 0), new(1, "audio", 1, 1000, 0, 0)]);
        var edl = Text(MpvEdlRenderer.Render(plan, Origin, Ids, Identity, noChapters: true, partitions: MpvPartitions.SplitPictureAndSound));
        Assert.Equal("# mpv EDL v0\n!no_chapters\n!delay_open,media_type=video\n" +
            $"{Src},0.000,5.479995\n{Src},10.519995,9.480005\n!new_stream\n!delay_open,media_type=audio\n" +
            $"{Src},0.000,5.480005\n{Src},10.520005,9.479995\n", edl);
        // Joint output for a source at file zero is unchanged from adapter revision 1 (the hard-fixture golden).
        Assert.Equal(Fixture("hard-fixture.mpv.edl"), RequireBytes(MpvEdlRenderer.Render(Require(Parse(Golden())), Origin, Ids, Identity)));
    }

    [Fact]
    public void MatroskaBoundariesCanSitJustBeforeTheInstant()
    {
        // Matroska AC-3: sound starts at -5 ms, so source [5485, 10525) is file time [5.480, 10.520), each moved 5 us earlier.
        var plan = MkvPlan(20000, 5485, 10525, [new(0, "video", 1, 1000, 0, 5), new(1, "audio", 1, 1000, -5, 0)]);
        var edl = Text(MpvEdlRenderer.Render(plan, Origin, Ids, Identity, noChapters: true, partitions: MpvPartitions.JointBeforeInstant));
        Assert.Equal($"# mpv EDL v0\n!no_chapters\n{Src},0.000,5.479995\n{Src},10.519995,9.475005\n", edl);
    }

    [Fact]
    public void AnUnverifiableFileClockRefusesTheWholeList()
    {
        var plan = MkvPlan(20021, 5501, 10541, [MkvAacClocks[0], Mp4Clocks[1]]);
        Assert.Equal(RefusalCode.UnverifiedPlayerClock, MpvEdlRenderer.Render(plan, Origin, Ids, Identity).Reason);
    }

    [Fact]
    public void DecodedPcmEvidenceNeverAdmitsPassthroughOrAnotherDevice()
    {
        // AC-3/E-AC-3 bitstreamed to a receiver cannot be cut to the sample by any player (only whole coded frames,
        // decoded downstream): it is a different output mode, so it needs its own row, and the design refuses it.
        var source = new EvidenceSource("mkv", "h264", "eac3",
            "0 video h264 High 8 bit SDR 1280x720 25 fps tb 1/1000 at 5 ms, 1 audio eac3 6 ch 48000 Hz tb 1/1000 at 0 ms plays", 20000);
        var pcm = new EvidenceTuple("mpv-edl", MpvEdlRenderer.AdapterRevision, "mpv", "0.40.0", new string('a', 64), "mpv",
            "Linux lab container, desktop mpv, decoded PCM capture, ad-lavc-o cons_noisegen 1", "10.11.11", new string('b', 64), source);
        var passthrough = pcm with { DeviceMode = "Linux lab container, desktop mpv, HDMI passthrough of AC-3 and E-AC-3" };
        var register = EvidenceRegister.Parse(Encoding.UTF8.GetBytes(System.Text.Json.JsonSerializer.Serialize(new
        {
            registerVersion = 1,
            rows = new object[] { Row(pcm, "experimental"), Row(passthrough, "refused") },
        }))).Value!;
        Assert.True(register.Admit(pcm, ["cut"], allowExperimental: true).Accepted);
        Assert.Equal(RefusalCode.EvidenceRefused, register.Admit(passthrough, ["cut"], allowExperimental: true).Reason);
        foreach (var other in new[] { pcm with { DeviceMode = "Google TV emulator, decoded PCM" }, pcm with { AppId = "Wholphin" },
                                      pcm with { JellyfinVersion = "12.4.0" }, pcm with { Source = source with { AudioCodec = "ac3" } } })
            Assert.Equal(RefusalCode.EvidenceTupleUnknown, register.Admit(other, ["cut"], allowExperimental: true).Reason);
    }

    private static object Row(EvidenceTuple t, string status) => new
    {
        tuple = new
        {
            adapterId = t.AdapterId, adapterRevision = t.AdapterRevision, appId = t.AppId, appVersion = t.AppVersion,
            appBuild = t.AppBuild, backend = t.Backend, deviceMode = t.DeviceMode, jellyfinVersion = t.JellyfinVersion,
            pluginBuild = t.PluginBuild,
            source = new { container = t.Source.Container, videoCodec = t.Source.VideoCodec, audioCodec = t.Source.AudioCodec,
                           trackLayout = t.Source.TrackLayout, durationMs = t.Source.DurationMs },
        },
        actions = new[] { "cut" },
        controls = new[] { "straight-play", "seek-forward", "seek-back" },
        results = new
        {
            markedPicture = new { outcome = "pass", limit = "test" }, markedSound = new { outcome = "pass", limit = "test" },
            keptPictureAtJoin = new { outcome = "pass", limit = "test" }, keptSoundAtJoin = new { outcome = "pass", limit = "test" },
        },
        evidenceRef = "tools/playback-lab/format-matrix.py",
        date = "2026-11-24",
        status,
        observation = "test",
    };

    private static string Src => $"%{Encoding.UTF8.GetByteCount(Source)}%{Source}";
    private static string Source => Origin.OriginalString.TrimEnd('/') +
        "/Cleanyfin/play/00000000000000000000000000000001/source?t=00000000000000000000000000000002";

    private static PlaybackPlan MkvPlan(long duration, long start, long end, ImmutableArray<TrackClock> clocks)
    {
        var (bytes, context) = WithCuts(duration, (start, end));
        var json = System.Text.Json.Nodes.JsonNode.Parse(bytes)!.AsObject();
        var tracks = new System.Text.Json.Nodes.JsonArray();
        foreach (var c in clocks)
            tracks.Add(new System.Text.Json.Nodes.JsonObject
            {
                ["index"] = c.Index, ["kind"] = c.Kind,
                ["timeBase"] = new System.Text.Json.Nodes.JsonArray(c.TimeBaseNumerator, c.TimeBaseDenominator),
                ["anchorPts"] = c.AnchorPts, ["anchorSourceMs"] = c.AnchorSourceMs,
            });
        json["source"]!["tracks"] = tracks;
        return Require(Parse(FixtureContract.Bytes(json), context with { Source = context.Source with { Tracks = clocks } }));
    }

    private static string Text(Decision<byte[]> d)
    {
        Assert.True(d.Accepted, d.Reason?.ToString());
        return Encoding.UTF8.GetString(d.Value!);
    }

    private static byte[] RequireBytes(Decision<byte[]> d)
    {
        Assert.True(d.Accepted, d.Reason?.ToString());
        return d.Value!;
    }
}
