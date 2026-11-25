using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Cleanyfin.Delivery;
using Json.Schema;

namespace Delivery.Contract.Tests;

/// <summary>
/// Edition binding: the exact bytes and timeline a reviewer reviewed. Real files in a scratch directory; the stream
/// layout comes from canned ffprobe JSON (what the server's probe prints), so these run without media tools.
/// </summary>
[Trait("Category", "Edition")]
public sealed class EditionTests : IDisposable
{
    private const string Item = "0123456789abcdef0123456789abcdef";
    private readonly string _dir = Path.Combine(Path.GetTempPath(), "cleanyfin-edition-tests", Guid.NewGuid().ToString("N"));

    public EditionTests() => Directory.CreateDirectory(_dir);
    public void Dispose() => Directory.Delete(_dir, true);

    // The hard fixture as ffprobe 7 (jellyfin-ffmpeg) prints it: 20 s, H.264 1/12800 and AAC 1/48000 from zero.
    private const string FixtureProbe = """
        {"programs":[],"stream_groups":[],"streams":[
          {"index":0,"codec_name":"h264","codec_type":"video","time_base":"1/12800","start_pts":0},
          {"index":1,"codec_name":"aac","codec_type":"audio","time_base":"1/48000","start_pts":0}],
         "format":{"format_name":"mov,mp4,m4a,3gp,3g2,mj2","start_time":"0.000000","duration":"20.000000"}}
        """;

    private static EditionLayout Layout(string json = FixtureProbe) =>
        EditionLayout.ParseFfprobe(Encoding.UTF8.GetBytes(json));

    private string Movie(string name, byte[] bytes)
    {
        var path = Path.Combine(_dir, name);
        File.WriteAllBytes(path, bytes);
        return path;
    }

    private static byte[] Bytes(int size, byte seed)
    {
        var b = new byte[size];
        new Random(seed).NextBytes(b);
        return b;
    }

    private static EditionRecord Hash(string path, EditionLayout? layout = null)
    {
        var h = EditionHasher.Verify(path, _ => layout ?? Layout(), 0, null, CancellationToken.None);
        Assert.NotNull(h);
        return new EditionRecord(Item, Item, path, h!.Stamp, h.Sha256, h.Layout, DateTimeOffset.UnixEpoch);
    }

    private static bool Current(EditionRecord r, string path) => r.IsCurrent(Item, Item, path, FileStamp.Read(path));

    // ---------- identity ----------

    [Fact]
    public void IdentityIsTheFullHashOfTheBytesPlusTheProbedTimeline()
    {
        var bytes = Bytes(3 * EditionHasher.ChunkBytes + 17, 1);
        var path = Movie("Movie (2001).mkv", bytes);
        var r = Hash(path);
        Assert.Equal(Convert.ToHexStringLower(SHA256.HashData(bytes)), r.Sha256);
        Assert.True(Current(r, path));

        var b = r.ToBinding("home-A");
        Assert.Equal(("home-A", Item, Item, r.Sha256, (long)bytes.Length, 20_000L), (b.ServerRef, b.ItemId, b.MediaSourceId, b.Sha256, b.SizeBytes, b.DurationMs));
        Assert.Equal("bind:" + Item + ":" + r.EditionSha256, b.BindingRev);
        Assert.Equal("clock:" + Item + ":" + r.LayoutSha256, b.TimelineRef);
        Assert.Equal([new(0, "video", 1, 12800, 0, 0), new TrackClock(1, "audio", 1, 48000, 0, 0)], b.Tracks.ToArray());
        // The binding is an ordinary snapshot-v1 binding: the existing strict reader and resolver accept it.
        var snapshot = new AcceptedAnnotationSnapshot("home:snap:1", PlanValidator.ReviewedCoverage, DateTimeOffset.UnixEpoch,
            DateTimeOffset.UnixEpoch.AddDays(1), b, [Annotation()]);
        Assert.True(ResolverInputReader.ParseSnapshot(SnapshotBytes(snapshot)).Accepted);

        // Same bytes, same probe: the same edition wherever and whenever it is hashed.
        var copy = Movie("copy.mkv", bytes);
        Assert.Equal(r.EditionSha256, Hash(copy).EditionSha256);
    }

    [Fact]
    public void TheRecordRoundTripsValidatesAgainstItsSchemaAndRefusesAHandEditedClock()
    {
        var path = Movie("a.mp4", Bytes(4096, 2));
        var r = Hash(path);
        var bytes = r.Write();
        Assert.True(SchemaAccepts("edition-record-v1.schema.json", bytes));
        var back = EditionRecord.Parse(bytes);
        Assert.True(back.Accepted, back.Reason?.ToString());
        Assert.Equal(r.EditionSha256, back.Value!.EditionSha256);
        Assert.Equal(r.Stamp, back.Value.Stamp);
        Assert.True(Current(back.Value, path));

        var moved = JsonNode.Parse(bytes)!;
        moved["layout"]!["streams"]![0]!["startPts"] = 1280;
        Assert.Equal(RefusalCode.InvalidDigest, EditionRecord.Parse(Encoding.UTF8.GetBytes(moved.ToJsonString())).Reason);
        var extra = JsonNode.Parse(bytes)!;
        extra["trusted"] = true;
        Assert.Equal(RefusalCode.UnknownField, EditionRecord.Parse(Encoding.UTF8.GetBytes(extra.ToJsonString())).Reason);
        var v2 = JsonNode.Parse(bytes)!;
        v2["editionVersion"] = 2;
        Assert.Equal(RefusalCode.UnsupportedVersion, EditionRecord.Parse(Encoding.UTF8.GetBytes(v2.ToJsonString())).Reason);
    }

    // ---------- invalidation ----------

    [Fact]
    public void ReplacingWithSameSizeAndSameDurationIsANewEdition()
    {
        var path = Movie("m.mp4", Bytes(1 << 16, 3));
        var r = Hash(path);
        var other = Bytes(1 << 16, 4);
        var tmp = Movie("m.mp4.part", other);
        File.SetLastWriteTimeUtc(tmp, File.GetLastWriteTimeUtc(path));
        File.Move(tmp, path, true); // what a download manager or "replace" does: same name, size, mtime, duration
        Assert.False(Current(r, path));
        var again = Hash(path);
        Assert.Equal(EditionChange.Content, r.Compare(again));
        Assert.NotEqual(r.ToBinding("h").BindingRev, again.ToBinding("h").BindingRev);
    }

    [Fact]
    public void AnInPlaceEditWithMtimePreservedIsCaughtByTheChangeTime()
    {
        var bytes = Bytes(1 << 16, 5);
        var path = Movie("m.mp4", bytes);
        var mtime = new DateTime(2024, 5, 1, 12, 0, 0, DateTimeKind.Utc); // a whole second, so it restores exactly
        File.SetLastWriteTimeUtc(path, mtime);
        var r = Hash(path);
        Thread.Sleep(20);
        using (var f = new FileStream(path, FileMode.Open, FileAccess.Write))
        {
            f.Position = 100;
            f.WriteByte((byte)(bytes[100] ^ 0xff));
        }
        File.SetLastWriteTimeUtc(path, mtime); // "touch -r": size and mtime as before
        Assert.Equal(r.Stamp.Size, FileStamp.Read(path).Size);
        Assert.Equal((r.Stamp.ModifiedSeconds, r.Stamp.ModifiedNanoseconds), (FileStamp.Read(path).ModifiedSeconds, FileStamp.Read(path).ModifiedNanoseconds));
        Assert.False(Current(r, path));
        Assert.Equal(EditionChange.Content, r.Compare(Hash(path)));
    }

    [Fact]
    public void ATouchOrAHardLinkForcesAReHashThatGivesBackTheSameEdition()
    {
        var path = Movie("m.mp4", Bytes(1 << 16, 6));
        var r = Hash(path);
        Thread.Sleep(20);
        File.SetLastWriteTimeUtc(path, DateTime.UtcNow);
        Assert.False(Current(r, path));
        var touched = Hash(path);
        Assert.Equal(EditionChange.None, r.Compare(touched));
        Assert.Equal(r.ToBinding("h").BindingRev, touched.ToBinding("h").BindingRev);

        Thread.Sleep(20);
        var link = Path.Combine(_dir, "hardlink.mp4");
        File.CreateSymbolicLink(Path.Combine(_dir, "unrelated"), path); // a symlink elsewhere changes nothing on the file
        Assert.True(Current(touched, path));
        Assert.Equal(0, Link(path, link)); // a new hard link changes the inode's link count and ctime
        Assert.False(Current(touched, path));
        var linked = Hash(path);
        Assert.Equal(touched.ToBinding("h").BindingRev, linked.ToBinding("h").BindingRev);
        // The other name is the same inode, but it is not the path that was verified.
        Assert.False(linked.IsCurrent(Item, Item, link, FileStamp.Read(link)));
    }

    [Fact]
    public void ARenameOrMoveIsNotTheVerifiedPath()
    {
        var path = Movie("m.mp4", Bytes(4096, 7));
        var r = Hash(path);
        var moved = Path.Combine(_dir, "Movies", "m.mp4");
        Directory.CreateDirectory(Path.GetDirectoryName(moved)!);
        File.Move(path, moved);
        Assert.False(r.IsCurrent(Item, Item, moved, FileStamp.Read(moved)));
        Assert.Throws<EditionRefused>(() => FileStamp.Read(path));
        // Re-hashed at its new path, the bytes and timeline are the same edition.
        Assert.Equal(EditionChange.None, r.Compare(Hash(moved)));
    }

    [Fact]
    public void AReEncodeChangesBytesAndTimeline()
    {
        var path = Movie("m.mkv", Bytes(1 << 16, 8));
        var r = Hash(path);
        File.WriteAllBytes(path, Bytes(1 << 15, 9));
        Assert.False(Current(r, path));
        var reencoded = Hash(path, Layout(FixtureProbe.Replace("\"h264\"", "\"hevc\"").Replace("1/12800", "1/1000")));
        Assert.Equal(EditionChange.Content, r.Compare(reencoded));
        Assert.NotEqual(r.LayoutSha256, reencoded.LayoutSha256);
    }

    [Fact]
    public void TheSameBytesReadAsADifferentTimelineIsANewEdition()
    {
        var path = Movie("m.mp4", Bytes(4096, 10));
        var r = Hash(path);
        // e.g. a probe upgrade that now reports an edit list's start offset
        var shifted = Hash(path, Layout(FixtureProbe.Replace("\"time_base\":\"1/12800\",\"start_pts\":0", "\"time_base\":\"1/12800\",\"start_pts\":1280")));
        Assert.Equal(EditionChange.Timeline, r.Compare(shifted));
        Assert.NotEqual(r.ToBinding("h").BindingRev, shifted.ToBinding("h").BindingRev);
    }

    [Fact]
    public void AFileThatChangesWhileItIsHashedGivesNoResult()
    {
        var path = Movie("m.mp4", Bytes(4 * EditionHasher.ChunkBytes, 11));
        var once = false;
        var result = EditionHasher.Verify(path, _ => Layout(), 0, (done, _) =>
        {
            if (done > 0 && !once)
            {
                once = true;
                using var f = new FileStream(path, FileMode.Open, FileAccess.Write);
                f.Position = 3 * EditionHasher.ChunkBytes;
                f.WriteByte(1);
            }
        }, CancellationToken.None);
        Assert.Null(result);
    }

    [Fact]
    public void PacingSpreadsTheHashOverTime()
    {
        var path = Movie("m.mp4", Bytes(2 * EditionHasher.ChunkBytes, 12));
        var slept = TimeSpan.Zero;
        var h = EditionHasher.Verify(path, _ => Layout(), EditionHasher.ChunkBytes, null, CancellationToken.None, d => slept += d);
        Assert.NotNull(h);
        Assert.True(slept > TimeSpan.FromSeconds(1.5), slept.ToString());
    }

    // ---------- clock and refusals ----------

    [Fact]
    public void AnchorsAreTheStreamsOwnFirstTimestampsOnOneMillisecondClock()
    {
        // Video starts 80 ms after audio (pts 1024 at 1/12800); the container start is the earlier stream.
        var layout = Layout(FixtureProbe.Replace("\"time_base\":\"1/12800\",\"start_pts\":0", "\"time_base\":\"1/12800\",\"start_pts\":1024"));
        Assert.Equal([new(0, "video", 1, 12800, 1024, 80), new TrackClock(1, "audio", 1, 48000, 0, 0)], layout.Clocks().ToArray());
        // MPEG-TS style: both start at 10 s (pts 900000 at 1/90000); source zero is that start.
        var ts = Layout(FixtureProbe.Replace("1/12800\",\"start_pts\":0", "1/90000\",\"start_pts\":900000")
            .Replace("1/48000\",\"start_pts\":0", "1/90000\",\"start_pts\":900000").Replace("\"start_time\":\"0.000000\"", "\"start_time\":\"10.000000\""));
        Assert.Equal([new(0, "video", 1, 90000, 900000, 0), new TrackClock(1, "audio", 1, 90000, 900000, 0)], ts.Clocks().ToArray());
        // Subtitles and attachments are in the layout digest, not on the clock.
        var withSubs = Layout(FixtureProbe.Replace("\"start_pts\":0}],", "\"start_pts\":0},{\"index\":2,\"codec_name\":\"subrip\",\"codec_type\":\"subtitle\",\"time_base\":\"1/1000\",\"start_pts\":0}],"));
        Assert.Equal(2, withSubs.Clocks().Length);
        Assert.NotEqual(Layout().Digest(), withSubs.Digest());
    }

    [Fact]
    public void AClockThatCannotBeWrittenExactlyIsRefused()
    {
        // AAC priming at -1024/48000 s (-21.333 ms): video sits 21.333 ms after zero, not a whole millisecond.
        var primed = Layout(FixtureProbe.Replace("\"time_base\":\"1/48000\",\"start_pts\":0", "\"time_base\":\"1/48000\",\"start_pts\":-1024")
            .Replace("\"start_time\":\"0.000000\"", "\"start_time\":\"-0.021333\""));
        Assert.Equal(RefusalCode.UnsupportedSourceClock, Assert.Throws<EditionRefused>(() => primed.Clocks()).Reason);
        // The probe's start must agree with the streams it reports.
        var disagree = Layout(FixtureProbe.Replace("\"start_time\":\"0.000000\"", "\"start_time\":\"1.000000\""));
        Assert.Equal(RefusalCode.UnsupportedSourceClock, Assert.Throws<EditionRefused>(() => disagree.Clocks()).Reason);
        var noPts = Layout(FixtureProbe.Replace(",\"start_pts\":0}", "}"));
        Assert.Equal(RefusalCode.UnsupportedSourceClock, Assert.Throws<EditionRefused>(() => noPts.Clocks()).Reason);
    }

    [Theory]
    [InlineData("""{"streams":[],"format":{"format_name":"mov","start_time":"0.000000","duration":"1.000000"}}""")]
    [InlineData("""{"streams":[{"index":1,"codec_type":"video","time_base":"1/25","start_pts":0}],"format":{"format_name":"mov","start_time":"0.000000","duration":"1.000000"}}""")]
    [InlineData("""{"streams":[{"index":0,"codec_type":"video","time_base":"0/25","start_pts":0}],"format":{"format_name":"mov","start_time":"0.000000","duration":"1.000000"}}""")]
    [InlineData("""{"streams":[{"index":0,"codec_type":"video","time_base":"1/25","start_pts":0,"x":1}],"format":{"format_name":"mov","start_time":"0.000000","duration":"1.000000"}}""")]
    [InlineData("""{"streams":[{"index":0,"codec_type":"video","time_base":"1/25","start_pts":0}],"format":{"format_name":"mov","start_time":"0.0000001","duration":"1.000000"}}""")]
    [InlineData("""{"streams":[{"index":0,"codec_type":"video","time_base":"1/25","start_pts":0}],"format":{"format_name":"mov","duration":"1.000000"}}""")]
    [InlineData("""not json""")]
    [InlineData("""{"streams":[{"index":0,"codec_type":"audio","time_base":"1/48000","start_pts":0,"side_data_list":[{"side_data_type":"x"}]}],"format":{"format_name":"mov","start_time":"0.000000","duration":"1.000000"}}""")]
    [InlineData("""{"streams":[{"index":0,"codec_type":"audio","time_base":"1/48000","start_pts":0,"side_data_list":[1]}],"format":{"format_name":"mov","start_time":"0.000000","duration":"1.000000"}}""")]
    public void AMalformedOrUnexpectedProbeIsRefused(string json) =>
        Assert.Equal(RefusalCode.UnverifiedBinding, Assert.Throws<EditionRefused>(() => EditionLayout.ParseFfprobe(Encoding.UTF8.GetBytes(json))).Reason);

    [Fact]
    public void EAc3InMp4ProbesWithAnEmptySideDataListAndBinds()
    {
        // jellyfin-ffmpeg 7 on an ffmpeg-muxed MP4 with E-AC-3 5.1: an empty side-data entry nobody asked for.
        var layout = Layout("""
            {"programs":[],"stream_groups":[],"streams":[
              {"index":0,"codec_name":"h264","codec_type":"video","time_base":"1/12800","start_pts":0},
              {"index":1,"codec_name":"eac3","codec_type":"audio","time_base":"1/48000","start_pts":0,"side_data_list":[{}]}],
             "format":{"format_name":"mov,mp4,m4a,3gp,3g2,mj2","start_time":"0.000000","duration":"20.000000"}}
            """);
        Assert.Equal("eac3", layout.Streams[1].Codec);
        Assert.Equal(Layout().Canonical().Replace(" aac ", " eac3 "), layout.Canonical());
    }

    [Fact]
    public void NotARegularFileIsRefused()
    {
        Assert.Throws<EditionRefused>(() => FileStamp.Read(_dir));
        Assert.Throws<EditionRefused>(() => FileStamp.Read(Path.Combine(_dir, "missing.mp4")));
    }

    // ---------- helpers ----------

    [System.Runtime.InteropServices.DllImport("libc", SetLastError = true)]
    private static extern int link(string oldpath, string newpath);
    private static int Link(string from, string to) => link(from, to);

    private static AcceptedAnnotation Annotation()
    {
        var a = new AcceptedAnnotation("m1", "", "violence", 2, "skip", 5480, 10520);
        return a with { ContentSha256 = a.ComputeContentSha256() };
    }

    private static byte[] SnapshotBytes(AcceptedAnnotationSnapshot s)
    {
        var b = s.Binding;
        var tracks = new JsonArray(b.Tracks.Select(t => (JsonNode)new JsonObject
        {
            ["index"] = t.Index, ["kind"] = t.Kind, ["timeBase"] = new JsonArray(t.TimeBaseNumerator, t.TimeBaseDenominator),
            ["anchorPts"] = t.AnchorPts, ["anchorSourceMs"] = t.AnchorSourceMs,
        }).ToArray());
        var a = s.Annotations[0];
        return Encoding.UTF8.GetBytes(new JsonObject
        {
            ["snapshotVersion"] = 1, ["snapshotRev"] = s.SnapshotRev, ["coverage"] = s.Coverage,
            ["acceptedAt"] = "1970-01-01T00:00:00Z", ["expiresAt"] = "1970-01-02T00:00:00Z",
            ["binding"] = new JsonObject
            {
                ["serverRef"] = b.ServerRef, ["itemId"] = b.ItemId, ["mediaSourceId"] = b.MediaSourceId, ["sha256"] = b.Sha256,
                ["sizeBytes"] = b.SizeBytes, ["timelineRef"] = b.TimelineRef, ["bindingRev"] = b.BindingRev, ["durationMs"] = b.DurationMs,
                ["origin"] = b.Origin, ["tracks"] = tracks,
            },
            ["annotations"] = new JsonArray(new JsonObject
            {
                ["ref"] = a.Ref, ["contentSha256"] = a.ContentSha256, ["category"] = a.Category, ["severity"] = a.Severity,
                ["action"] = a.Action, ["startMs"] = a.StartMs, ["endMs"] = a.EndMs,
            }),
        }.ToJsonString());
    }

    private static bool SchemaAccepts(string schema, byte[] json) => JsonSchema.FromText(File.ReadAllText(
        Path.Combine(AppContext.BaseDirectory, schema))).Evaluate(JsonNode.Parse(json),
        new EvaluationOptions { RequireFormatValidation = true }).IsValid;
}
