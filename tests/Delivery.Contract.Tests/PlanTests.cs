using System.Collections.Immutable;
using System.Numerics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Cleanyfin.Delivery;
using static Delivery.Contract.Tests.FixtureContract;

namespace Delivery.Contract.Tests;

[Trait("Category", "Plan")]
public class PlanTests
{
    [Fact]
    public void GoldenV0ConvertsExactlyToValidatedV1()
    {
        var rawV0 = Fixture("hard-fixture.v0.json");
        Assert.False(SchemaAccepts(rawV0)); // v0 is an oracle, not a v1 playback instruction.
        Assert.False(Parse(rawV0).Accepted);
        var converted = ConvertLabFixture(rawV0);
        Assert.True(JsonNode.DeepEquals(JsonNode.Parse(Golden()), converted));
        Assert.True(SchemaAccepts(Bytes(converted)));
        Assert.True(SchemaAccepts(Golden()));
        var plan = Require(Parse(Golden()));
        var cut = Assert.Single(plan.Operations);
        Assert.Equal((5480L, 10520L), (cut.StartMs, cut.EndMs));
        Assert.Equal(14960, plan.Timeline.FilteredDurationMs);
        Assert.Equal(149600000, CutTimeline.MillisecondsToTicks(plan.Timeline.FilteredDurationMs));
        Assert.Equal(10520, plan.Timeline.FilteredToSource(5480));
        Assert.Equal(12040, plan.Timeline.FilteredToSource(7000));
    }

    [Theory]
    [InlineData("/planVersion", "2", RefusalCode.UnsupportedVersion)]
    [InlineData("/requires", "[\"mute\"]", RefusalCode.UnsupportedFeature)]
    [InlineData("/requires", "[\"cut\",\"cut\"]", RefusalCode.DuplicateReference)]
    [InlineData("/requires", "[]", RefusalCode.MissingRequiredFeature)]
    [InlineData("/operations/0/action", "\"mute\"", RefusalCode.UnsupportedAction)]
    [InlineData("/operations/0/startMs", "5480.1", RefusalCode.InvalidValue)]
    [InlineData("/operations/0/startMs", "-1", RefusalCode.InvalidInterval)]
    [InlineData("/operations/0/extra", "1", RefusalCode.UnknownField)]
    [InlineData("/source/tracks/0/dts", "0", RefusalCode.UnknownField)]
    [InlineData("/source/tracks/0/timeBase", "[0,12800]", RefusalCode.InvalidValue)]
    [InlineData("/source/tracks/0/timeBase", "[1,12800,0]", RefusalCode.InvalidValue)]
    [InlineData("/source/origin", "\"dts-zero\"", RefusalCode.InvalidValue)]
    [InlineData("/source/sha256", "\"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\"", RefusalCode.InvalidDigest)]
    [InlineData("/inputs/annotations/0/contentSha256", "\"abcd\"", RefusalCode.InvalidDigest)]
    [InlineData("/operations/0/from", "[]", RefusalCode.InvalidValue)]
    [InlineData("/operations/0/from", "[\"home:fixture:mark1\",\"home:fixture:mark1\"]", RefusalCode.DuplicateReference)]
    [InlineData("/source/sizeBytes", "9007199254740992", RefusalCode.IntegerOverflow)]
    [InlineData("/issuedAt", "\"2026-11-21T18:00:00+00:00\"", RefusalCode.InvalidTimestamp)]
    [InlineData("/issuedAt", "\"2026-11-21T19:00:00+01:00\"", RefusalCode.InvalidTimestamp)]
    [InlineData("/issuedAt", "\"not-a-timestamp\"", RefusalCode.InvalidTimestamp)]
    [InlineData("/issuedAt", "\"2026-11-21T18:00:00.00000001Z\"", RefusalCode.InvalidTimestamp)]
    [InlineData("/extensions", "{\"sourceUrl\":\"https://elsewhere.example\"}", RefusalCode.UnknownField)]
    [InlineData("/extensions", "{\"description\":\"https://elsewhere.example\"}", RefusalCode.InvalidValue)]
    [InlineData("/unknown", "true", RefusalCode.UnknownField)]
    public void SchemaAndRuntimeRejectStructuralInvalidSamples(string path, string value, RefusalCode reason)
    {
        var bytes = Replace(path, value);
        Assert.False(SchemaAccepts(bytes));
        Assert.Equal(reason, Parse(bytes).Reason);
    }

    [Fact]
    public void OptionalDescriptionIsBoundedAndIgnored()
    {
        var bytes = Replace("/extensions", "{\"description\":\"Reviewer's note (not an instruction).\"}");
        Assert.True(SchemaAccepts(bytes));
        var plan = Require(Parse(bytes));
        Assert.Equal(Context.ActiveRevision, plan.Revision);
        bytes = Change(root => root["extensions"] = new JsonObject { ["description"] = new string('d', 513) });
        Assert.False(SchemaAccepts(bytes));
        Assert.Equal(RefusalCode.LimitExceeded, Parse(bytes).Reason);
    }

    [Fact]
    public void DuplicateJsonKeysUseDecodedNamesEvenInExtensions()
    {
        var json = Encoding.UTF8.GetString(Golden());
        Assert.Equal(RefusalCode.DuplicateKey, Parse(Encoding.UTF8.GetBytes(json.Replace(
            "\"planVersion\": 1", "\"planVersion\": 1, \"plan\\u0056ersion\": 1"))).Reason);
        Assert.Equal(RefusalCode.DuplicateKey, Parse(Encoding.UTF8.GetBytes(json.Insert(1,
            "\"extensions\":{\"description\":\"One\",\"description\":\"Two\"},"))).Reason);
    }

    [Fact]
    public void MalformedAbsentAndValidEmptyAreDistinct()
    {
        Assert.Equal(RefusalCode.AbsentPlan, Parse([]).Reason);
        Assert.Equal(RefusalCode.InvalidJson, Parse([(byte)'{']).Reason);
        Assert.Equal(RefusalCode.InvalidJson, Parse([0xff]).Reason);
        var badEscape = Encoding.UTF8.GetString(Golden()).Replace(Context.ActiveRevision, "\\ud800");
        Assert.Equal(RefusalCode.InvalidJson, Parse(Encoding.UTF8.GetBytes(badEscape)).Reason);
        Assert.Equal(RefusalCode.MissingField, Parse(Change(p => p.Remove("operations"))).Reason);
        var (bytes, context) = WithCuts(20000);
        Assert.True(SchemaAccepts(bytes));
        var plan = Require(Parse(bytes, context));
        Assert.Empty(plan.Operations);
        Assert.Empty(plan.Inputs.Annotations);
        Assert.Equal(new KeptRange(0, 20000, 0), Assert.Single(plan.Timeline.KeptRanges));
        Assert.Equal(7000, plan.Timeline.FilteredToSource(7000));
        Assert.Equal(RefusalCode.UnverifiedBinding, Parse(bytes, null).Reason);
    }

    [Fact]
    public void ClockEnforcesHalfOpenValidityAndRollbackFloor()
    {
        Assert.True(Parse(Golden(), Context, Issued).Accepted);
        Assert.True(Parse(Golden(), Context, Expires.AddTicks(-1)).Accepted);
        Assert.Equal(RefusalCode.NotYetValid, Parse(Golden(), Context, Issued.AddTicks(-1)).Reason);
        Assert.Equal(RefusalCode.Expired, Parse(Golden(), Context, Expires).Reason);
        Assert.Equal(RefusalCode.ClockRollback, Parse(Golden(), Context with { ClockFloor = Now.AddTicks(1) }).Reason);
        Assert.Equal(RefusalCode.InvalidValidityWindow,
            Parse(Replace("/expiresAt", "\"2026-11-21T18:00:00Z\"")).Reason);
        // The test schema engine checks date-time syntax, not every impossible calendar date.
        Assert.Equal(RefusalCode.InvalidTimestamp, Parse(Replace("/issuedAt", "\"2026-02-30T18:00:00Z\"")).Reason);
        var precise = Replace("/issuedAt", "\"2026-11-21T18:00:00.0000001Z\"");
        Assert.True(SchemaAccepts(precise));
        Assert.Equal(Issued.AddTicks(1), Require(Parse(precise)).IssuedAt);
    }

    [Fact]
    public void RequireCurrentAuthorityRevisionAndSnapshot()
    {
        var alternate = Context.Authority with { ViewerRef = "local:v2", PolicyRev = "policy:8" };
        Assert.Equal(RefusalCode.AuthorityMismatch, Parse(Golden(), Context with { Authority = alternate }).Reason);
        var alternatePlan = Change(p =>
        {
            p["authority"]!["viewerRef"] = alternate.ViewerRef;
            p["authority"]!["policyRev"] = alternate.PolicyRev;
        });
        Assert.True(Parse(alternatePlan, Context with { Authority = alternate }).Accepted);
        Assert.Equal(RefusalCode.AuthorityMismatch, Parse(alternatePlan).Reason);
        Assert.Equal(RefusalCode.RevisionMismatch, Parse(Golden(), Context with { ActiveRevision = "local:plan:43" }).Reason);
        Assert.Equal(RefusalCode.SnapshotMismatch, Parse(Golden(), Context with { SnapshotRev = "home:review:10" }).Reason);
    }

    [Fact]
    public void ChangedAnnotationUnderSameRefAndMissingAcceptedInputAreRefused()
    {
        Assert.Equal(RefusalCode.AnnotationDigestMismatch, Parse(Golden(), Context with
        {
            AnnotationDigests = Context.AnnotationDigests.SetItem(Mark, new string('b', 64))
        }).Reason);
        Assert.Equal(RefusalCode.UnknownReference, Parse(Golden(), Context with
        {
            AnnotationDigests = ImmutableDictionary<string, string>.Empty
        }).Reason);
        Assert.Equal(RefusalCode.UnknownReference, Parse(Replace("/operations/0/from", "[\"not:accepted\"]")).Reason);
        Assert.Equal(RefusalCode.DuplicateReference, Parse(Change(p =>
            p["inputs"]!["annotations"]!.AsArray().Add(new JsonObject
            {
                ["ref"] = Mark, ["contentSha256"] = new string('b', 64)
            }))).Reason);
        Assert.Equal(RefusalCode.DuplicateReference, Parse(Change(p =>
            p["operations"]!.AsArray().Add(new JsonObject
            {
                ["action"] = "cut", ["startMs"] = 12000, ["endMs"] = 13000, ["from"] = new JsonArray(Mark)
            }))).Reason);
    }

    [Fact]
    public void SameLengthAndUnchangedEndsDoNotProveSourceBinding()
    {
        byte[] source = new byte[3 * 65536];
        var originalHash = Convert.ToHexStringLower(SHA256.HashData(source));
        var bytes = Change(p => { p["source"]!["sizeBytes"] = source.Length; p["source"]!["sha256"] = originalHash; });
        var original = Context.Source with { SizeBytes = source.Length, Sha256 = originalHash };
        Assert.True(Parse(bytes, Context with { Source = original }).Accepted);
        source[65536] = 1; // Identical first and last hash windows and size; different bytes.
        var changed = original with { Sha256 = Convert.ToHexStringLower(SHA256.HashData(source)) };
        Assert.Equal(RefusalCode.BindingMismatch, Parse(bytes, Context with { Source = changed }).Reason);
    }

    [Fact]
    public void TrackAnchorsAreExactRationalAndMustMatchVerifiedMapping()
    {
        var json = Change(p =>
        {
            p["source"]!["tracks"]![1]!["anchorPts"] = -4800;
            p["source"]!["tracks"]![1]!["anchorSourceMs"] = 137;
        });
        Assert.Equal(RefusalCode.BindingMismatch, Parse(json).Reason);
        Assert.Equal(RefusalCode.InvalidValue, Parse(Replace("/source/tracks/1/anchorSourceMs", "20000")).Reason);
        Assert.Equal(RefusalCode.DuplicateTrackIndex, Parse(Replace("/source/tracks/1/index", "0")).Reason);
        var audio = Context.Source.Tracks[1] with { AnchorPts = -4800, AnchorSourceMs = 137 };
        var verified = Context with { Source = Context.Source with { Tracks = Context.Source.Tracks.SetItem(1, audio) } };
        var plan = Require(Parse(json, verified));
        var (numerator, denominator) = plan.Source.Tracks[1].ToSourceSeconds(-4799);
        Assert.Equal(new BigInteger(137 * 48000 + 1000), numerator);
        Assert.Equal(new BigInteger(48000 * 1000), denominator);
    }

    [Fact]
    public void IntegerRepresentationAndTickOverflowAreCheckedWithoutRounding()
    {
        Assert.Equal(RefusalCode.InvalidValue, Parse(Replace("/source/durationMs", "20000.0")).Reason);
        Assert.Equal(RefusalCode.InvalidValue, Parse(Replace("/source/durationMs", "2e4")).Reason);
        Assert.Equal(RefusalCode.IntegerOverflow, Parse(Replace("/source/durationMs", "922337203685478")).Reason);
        Assert.Equal(RefusalCode.IntegerOverflow, Parse(Replace("/source/durationMs", "9223372036854775808")).Reason);
        long maxMs = long.MaxValue / TimeSpan.TicksPerMillisecond;
        Assert.Equal(maxMs * TimeSpan.TicksPerMillisecond, CutTimeline.MillisecondsToTicks(maxMs));
        Assert.Throws<OverflowException>(() => CutTimeline.MillisecondsToTicks(maxMs + 1));
        Assert.Equal(maxMs, PlanWithCuts(maxMs).Timeline.FilteredDurationMs);
    }

    [Fact]
    public void CheckByteAndArrayCapsBeforeWalkingCollections()
    {
        Assert.Equal(RefusalCode.PlanTooLarge, Parse(new byte[PlanValidator.MaxPlanBytes + 1]).Reason);
        Assert.Equal(RefusalCode.LimitExceeded, Parse(Change(p => p["operations"] =
            NullArray(PlanValidator.MaxOperations + 1))).Reason);
        Assert.Equal(RefusalCode.LimitExceeded, Parse(Change(p => p["inputs"]!["annotations"] =
            NullArray(PlanValidator.MaxInputReferences + 1))).Reason);
        Assert.Equal(RefusalCode.LimitExceeded, Parse(Change(p => p["operations"]![0]!["from"] =
            NullArray(PlanValidator.MaxInputReferences + 1))).Reason);
        Assert.Equal(RefusalCode.LimitExceeded, Parse(Change(p => p["source"]!["tracks"] =
            NullArray(PlanValidator.MaxTracks + 1))).Reason);
        Assert.Equal(RefusalCode.LimitExceeded, Parse(Change(p => p["revision"] =
            new string('x', PlanValidator.MaxReferenceLength + 1))).Reason);

        static JsonArray NullArray(int count) => new(new JsonNode?[count]);
    }

    private static byte[] Replace(string path, string value) => Change(root =>
    {
        var parts = path[1..].Split('/');
        JsonNode node = root;
        foreach (var part in parts[..^1])
            node = node is JsonArray ? node[int.Parse(part)]! : node[part]!;
        node[parts[^1]] = JsonNode.Parse(value);
    });
}
