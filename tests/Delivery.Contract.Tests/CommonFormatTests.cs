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

    private static readonly ImmutableArray<TrackClock> MkvAc3Clocks = [new(0, "video", 1, 1000, 0, 5), new(1, "audio", 1, 1000, -5, 0)];

    [Fact]
    public void SplitFormPutsPictureJustBeforeAndSoundHalfASampleAfterEveryBoundary()
    {
        var plan = MkvPlan(20000, 5480, 10520, [new(0, "video", 1, 12800, 0, 0), new(1, "audio", 1, 48000, 0, 0)]);
        var edl = Text(MpvEdlRenderer.Render(plan, Origin, Ids, Identity, noChapters: true,
            partitions: MpvPartitions.SplitPictureAndSound, soundSampleRate: 48000));
        // Sound: every mark edge 10 us (half of 20.83 us) after its sample instant; the presentation start and the end of
        // the file never moves. Picture: interior boundaries 2 us before the frame instant.
        Assert.Equal("# mpv EDL v0\n!no_chapters\n!delay_open,media_type=video\n" +
            $"{Src},0.000,5.479998\n{Src},10.519998,9.480002\n!new_stream\n!delay_open,media_type=audio\n" +
            $"{Src},0.000,5.480010\n{Src},10.520010,9.479990\n", edl);
        // Joint output for a source at file zero is unchanged from adapter revision 1 (the hard-fixture golden).
        Assert.Equal(Fixture("hard-fixture.mpv.edl"), RequireBytes(MpvEdlRenderer.Render(Require(Parse(Golden())), Origin, Ids, Identity)));
    }

    [Fact]
    public void SplitFormHandlesAMarkAtTheStartOrTheEndAndAMatroskaClock()
    {
        // Matroska AC-3: sound starts at -5 ms, so source [0, 1005) removes file time up to 1.000 s and the
        // presentation's own start is file time 0, not -5 ms.
        var lead = Text(MpvEdlRenderer.Render(MkvPlan(20005, 0, 1005, MkvAc3Clocks), Origin, Ids, Identity, noChapters: true,
            partitions: MpvPartitions.SplitPictureAndSound, soundSampleRate: 48000));
        Assert.Equal("# mpv EDL v0\n!no_chapters\n!delay_open,media_type=video\n" +
            $"{Src},0.999998,19.000002\n!new_stream\n!delay_open,media_type=audio\n{Src},1.000010,18.999990\n", lead);
        var tail = Text(MpvEdlRenderer.Render(MkvPlan(20005, 5485, 20005, MkvAc3Clocks), Origin, Ids, Identity, noChapters: true,
            partitions: MpvPartitions.SplitPictureAndSound, soundSampleRate: 48000));
        Assert.Equal("# mpv EDL v0\n!no_chapters\n!delay_open,media_type=video\n" +
            $"{Src},0.000,5.479998\n!new_stream\n!delay_open,media_type=audio\n{Src},0.000,5.480010\n", tail);
    }

    [Theory]
    [InlineData(44100)] // a whole millisecond is not a sample instant
    [InlineData(0)]
    [InlineData(384000)]
    public void SplitFormRefusesASoundRateWhoseMillisecondsAreNotSamples(int rate) =>
        Assert.Equal(RefusalCode.UnverifiedPlayerClock, MpvEdlRenderer.Render(MkvPlan(20005, 5485, 10525, MkvAc3Clocks), Origin, Ids,
            Identity, partitions: MpvPartitions.SplitPictureAndSound, soundSampleRate: rate).Reason);

    [Theory]
    [InlineData(1, 1000, true)]
    [InlineData(1, 12800, true)]
    [InlineData(1, 90000, true)]
    [InlineData(1001, 24000, true)]
    [InlineData(1, 1_000_000, false)] // a frame 1 us before a millisecond would be dropped by the 2 us lead
    [InlineData(1, 500_000, false)]
    public void SplitFormRefusesAPictureClockWithTicksInsideTheLead(long num, long den, bool clears)
    {
        Assert.Equal(clears, MpvEdlRenderer.PictureClockClearsLead(num, den));
        var plan = MkvPlan(20000, 5480, 10520, [new(0, "video", num, den, 0, 0), new(1, "audio", 1, 48000, 0, 0)]);
        Assert.Equal(clears, MpvEdlRenderer.Render(plan, Origin, Ids, Identity, partitions: MpvPartitions.SplitPictureAndSound,
            soundSampleRate: 48000).Accepted);
    }

    [Fact]
    public void EachCutListFormHasItsOwnEvidenceRevision()
    {
        string[] revisions = [MpvEdlRenderer.Revision(MpvPartitions.Joint, false), MpvEdlRenderer.Revision(MpvPartitions.Joint, true),
            MpvEdlRenderer.Revision(MpvPartitions.SplitPictureAndSound, false), MpvEdlRenderer.Revision(MpvPartitions.SplitPictureAndSound, true)];
        Assert.Equal(["2", "3", "4", "5"], revisions);
        Assert.Equal([false, true, false, true], revisions.Select(MpvEdlRenderer.IsSubtitleRevision));
    }

    /// <summary>
    /// mpv 0.40's sound boundary rule, simulated exactly as written (audio/aframe.c): an audio frame starting at time p
    /// keeps <c>(int)((end - p) * rate)</c> samples before a segment end and skips <c>(int)((start - p) * rate)</c> after
    /// a segment start, in double arithmetic on the EDL's decimal seconds. Frame times carry up to a nanosecond of
    /// container error either way (Matroska stores CodecDelay in whole ns). The split form keeps exactly the samples
    /// outside the mark at every whole-millisecond cut position; a boundary on the instant does not.
    /// </summary>
    [Fact]
    public void SplitSoundBoundariesKeepExactlyTheUnmarkedSamplesAtEveryMillisecond()
    {
        const int rate = 48000, frame = 1536;
        var lag = MpvEdlRenderer.SoundLagMicroseconds(rate)!.Value;
        int jointFailures = 0;
        foreach (var error in new[] { -1e-9, 0.0, 1e-9 })
            for (long ms = 1; ms < 20000; ms++)
            {
                var sample = ms * rate / 1000; // the first sample at or after the boundary instant
                var p = (sample / frame) * frame / (double)rate + error; // the coded frame containing it
                var split = double.Parse(Seconds(ms * 1000 + lag), System.Globalization.CultureInfo.InvariantCulture);
                Assert.Equal(sample - (sample / frame) * frame, (long)((split - p) * rate));
                var joint = double.Parse(Seconds(ms * 1000), System.Globalization.CultureInfo.InvariantCulture);
                jointFailures += (long)((joint - p) * rate) != sample - (sample / frame) * frame ? 1 : 0;
            }
        Assert.True(jointFailures > 0, "a boundary on the sample instant is a tie");

        static string Seconds(long us) => FormattableString.Invariant($"{us / 1_000_000}.{us % 1_000_000:D6}");
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
