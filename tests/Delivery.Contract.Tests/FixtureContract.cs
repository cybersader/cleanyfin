using System.Collections.Immutable;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Cleanyfin.Delivery;
using Json.Schema;

namespace Delivery.Contract.Tests;

internal static class FixtureContract
{
    internal const string Mark = "home:fixture:mark1";
    internal const string MarkDigest = "06abcdc0c8b60fb497da40742ad1c354015cb99c06126a5353dbff6e5cdcb792";
    internal static readonly string SourceDigest = new('a', 64);
    internal static readonly DateTimeOffset Issued = new(2026, 11, 21, 18, 0, 0, TimeSpan.Zero);
    internal static readonly DateTimeOffset Expires = Issued.AddMinutes(30);
    internal static readonly DateTimeOffset Now = Issued.AddMinutes(15);
    internal static readonly Uri Origin = new("https://jellyfin.example");
    internal static readonly MpvSourceIdentifiers Ids = new(1, 2);
    internal static readonly VerifiedPlanContext Context = new("local:plan:42",
        new SourceBinding("home-A", new string('1', 32), new string('1', 32), SourceDigest,
            1_240_000, "review:fixture:1", "binding:3", 20_000, "source-presentation-zero",
            [new(0, "video", 1, 12800, 0, 0), new(1, "audio", 1, 48000, 0, 0)]),
        new PlanAuthority("local:v1", "local:p2", "policy:7", "trust:2"), "home:review:9",
        "reviewed-marks-only", ImmutableDictionary<string, string>.Empty.Add(Mark, MarkDigest));

    private static readonly JsonSchema Schema = JsonSchema.FromText(File.ReadAllText(
        Path.Combine(AppContext.BaseDirectory, "playback-plan-v1.schema.json")));

    internal static byte[] Fixture(string name) => File.ReadAllBytes(Path.Combine(AppContext.BaseDirectory, "Fixtures", name));
    internal static byte[] Golden() => Fixture("hard-fixture.v1.json");
    internal static JsonObject PlanJson() => JsonNode.Parse(Golden())!.AsObject();
    internal static byte[] Bytes(JsonNode node) => JsonSerializer.SerializeToUtf8Bytes(node);
    internal static byte[] Change(Action<JsonObject> change)
    {
        var json = PlanJson();
        change(json);
        return Bytes(json);
    }

    internal static Decision<PlaybackPlan> Parse(byte[] bytes) => Parse(bytes, Context);
    internal static Decision<PlaybackPlan> Parse(byte[] bytes, VerifiedPlanContext? context, DateTimeOffset? now = null) =>
        PlanValidator.Parse(bytes, new FixedClock(now ?? Now), context);

    internal static PlaybackPlan Require(Decision<PlaybackPlan> decision)
    {
        Assert.True(decision.Accepted, decision.Reason?.ToString());
        return Assert.IsType<PlaybackPlan>(decision.Value);
    }

    internal static bool SchemaAccepts(byte[] bytes) => Schema.Evaluate(JsonNode.Parse(bytes),
        new EvaluationOptions { RequireFormatValidation = true }).IsValid;

    internal static (byte[] Bytes, VerifiedPlanContext Verified) WithCuts(long duration, params (long Start, long End)[] cuts)
    {
        var json = PlanJson();
        json["source"]!["durationMs"] = duration;
        json["requires"] = cuts.Length == 0 ? new JsonArray() : new JsonArray("cut");
        var operations = new JsonArray();
        var annotations = new JsonArray();
        var digests = ImmutableDictionary.CreateBuilder<string, string>(StringComparer.Ordinal);
        for (int i = 0; i < cuts.Length; i++)
        {
            var reference = FormattableString.Invariant($"test:cut:{i}");
            annotations.Add(new JsonObject { ["ref"] = reference, ["contentSha256"] = MarkDigest });
            var (start, end) = cuts[i];
            operations.Add(new JsonObject
            {
                ["action"] = "cut", ["startMs"] = start, ["endMs"] = end, ["from"] = new JsonArray(reference)
            });
            digests.Add(reference, MarkDigest);
        }
        json["operations"] = operations;
        json["inputs"]!["annotations"] = annotations;
        return (Bytes(json), Context with { Source = Context.Source with { DurationMs = duration }, AnnotationDigests = digests.ToImmutable() });
    }

    internal static PlaybackPlan PlanWithCuts(long duration, params (long Start, long End)[] cuts)
    {
        var (bytes, context) = WithCuts(duration, cuts);
        return Require(Parse(bytes, context));
    }

    // Test-only binder: v0 never attested a source binding. v1-only facts below come from a synthetic
    // reviewed fixture manifest (Context), not inferred from a frame count, digest or guessed track origin.
    internal static JsonObject ConvertLabFixture(byte[] labV0)
    {
        var v0 = JsonNode.Parse(labV0)!;
        var source = v0["source"]!;
        var cut = Assert.Single(v0["cuts"]!.AsArray())!;
        Assert.Equal(0, v0["version"]!.GetValue<int>());
        Assert.Equal(SourceDigest, source["sha256"]!.GetValue<string>());
        var fps = source["fps"]!.GetValue<long>();
        var sampleRate = source["sampleRate"]!.GetValue<long>();
        var startFrame = cut["startFrame"]!.GetValue<long>();
        var endFrame = cut["endFrame"]!.GetValue<long>();
        var start = ExactMilliseconds(startFrame);
        var end = ExactMilliseconds(endFrame);
        Assert.Equal(start, cut["startMs"]!.GetValue<long>());
        Assert.Equal(end, cut["endMs"]!.GetValue<long>());
        var duration = ExactMilliseconds(source["frames"]!.GetValue<long>());
        var video = cut["muxedVideo"]!;
        var baseParts = video["timeBase"]!.GetValue<string>().Split('/');
        var numerator = long.Parse(baseParts[0], CultureInfo.InvariantCulture);
        var denominator = long.Parse(baseParts[1], CultureInfo.InvariantCulture);
        Assert.Equal(start * denominator, video["firstMarkedPts"]!.GetValue<long>() * numerator * 1000);
        Assert.Equal(ExactMilliseconds(endFrame - 1) * denominator,
            video["lastMarkedPts"]!.GetValue<long>() * numerator * 1000);
        var digest = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(
            FormattableString.Invariant($"fixture:cut:{start}:{end}"))));
        return JsonSerializer.SerializeToNode(new
        {
            planVersion = 1, requires = new[] { "cut" }, revision = Context.ActiveRevision,
            issuedAt = Issued.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture),
            expiresAt = Expires.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture),
            source = new
            {
                serverRef = Context.Source.ServerRef, itemId = Context.Source.ItemId, mediaSourceId = Context.Source.MediaSourceId,
                sha256 = source["sha256"]!.GetValue<string>(), sizeBytes = Context.Source.SizeBytes,
                timelineRef = Context.Source.TimelineRef, bindingRev = Context.Source.BindingRev, durationMs = duration,
                origin = Context.Source.Origin,
                tracks = new[]
                {
                    Track(Context.Source.Tracks[0], numerator, denominator),
                    Track(Context.Source.Tracks[1], 1, sampleRate)
                }
            },
            authority = Context.Authority,
            inputs = new
            {
                snapshotRev = Context.SnapshotRev, coverage = Context.Coverage,
                annotations = new[] { new { @ref = Mark, contentSha256 = digest } }
            },
            operations = new[] { new { action = "cut", startMs = start, endMs = end, from = new[] { Mark } } }
        }, new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase })!.AsObject();

        long ExactMilliseconds(long frames)
        {
            var (ms, remainder) = Math.DivRem(checked(frames * 1000), fps);
            Assert.Equal(0, remainder);
            return ms;
        }

        static object Track(TrackClock track, long num, long den) => new
        {
            index = track.Index, kind = track.Kind, timeBase = new[] { num, den },
            anchorPts = track.AnchorPts, anchorSourceMs = track.AnchorSourceMs
        };
    }

    private sealed class FixedClock(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }
}
