using System.Collections.Immutable;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;
using Cleanyfin.Delivery;
using Json.Schema;
using static Delivery.Contract.Tests.FixtureContract;

namespace Delivery.Contract.Tests;

[Trait("Category", "Resolver")]
public class ResolverTests
{
    private const string SnapshotFile = "hard-fixture.snapshot.v1.json";
    private const string PolicyFile = "hard-fixture.policy.v1.json";
    private const string SnapshotSchema = "annotation-snapshot-v1.schema.json";
    private const string PolicySchema = "household-policy-v1.schema.json";
    private static readonly AcceptedAnnotationSnapshot Snapshot = ResolverInputReader.ParseSnapshot(Fixture(SnapshotFile)).Value!;
    private static readonly HouseholdPolicy Policy = ResolverInputReader.ParsePolicy(Fixture(PolicyFile)).Value!;
    private static readonly ResolverRequest Request = new(Context.Authority.ViewerRef, Context.Authority.ProfileRef,
        Context.Source.ServerRef, Identity(Context.Source), new FixedClock(Issued), Issued, TimeSpan.FromMinutes(30));
    private static readonly SequenceRevisions Revisions = new();

    [Fact]
    public void GoldenInputsValidateAndResolveToGoldenCutAndEdl()
    {
        Assert.True(SchemaAccepts(SnapshotSchema, Fixture(SnapshotFile)));
        Assert.True(SchemaAccepts(PolicySchema, Fixture(PolicyFile)));
        Assert.True(ResolverInputReader.ParseSnapshot(Fixture(SnapshotFile)).Accepted);
        Assert.True(ResolverInputReader.ParsePolicy(Fixture(PolicyFile)).Accepted);
        var result = Offer(Snapshot, Policy, revisions: new SequenceRevisions());
        var raw = result.Utf8.ToArray();
        Assert.True(FixtureContract.SchemaAccepts(raw));
        var validated = Require(Parse(raw, Context with
        {
            ActiveRevision = "local:resolved:1",
            AnnotationDigests = Context.AnnotationDigests.SetItem(Mark, Assert.Single(Snapshot.Annotations).ContentSha256),
            ClockFloor = Issued
        }));
        Assert.Equal(Issued, validated.IssuedAt);
        Assert.Equal(Expires, validated.ExpiresAt);
        Assert.Equal(["cut"], validated.Requires.ToArray());
        Assert.Equal(JsonNode.Parse(Golden())!["operations"]!.ToJsonString(), JsonNode.Parse(raw)!["operations"]!.ToJsonString());
        var rendered = MpvEdlRenderer.Render(validated, Origin, Ids, MpvClockMapping.VerifiedSourceIdentity);
        Assert.True(rendered.Accepted, rendered.Reason?.ToString());
        Assert.Equal(Fixture("hard-fixture.mpv.edl"), rendered.Value);
    }

    [Fact]
    public void TwoAuthenticatedProfilesResolveDifferentTimelines()
    {
        var snapshot = Marks(Snapshot.Annotations[0], Annotation("home:mark:nudity", 11_000, 12_000, category: "nudity"));
        var secondPolicy = new HouseholdPolicy("local:p3", "policy:8", "trust:3", [new("nudity", 0, true)]);
        var secondRequest = Request with { ViewerRef = "local:v2", ProfileRef = secondPolicy.ProfileRef };
        var first = Offer(snapshot, Policy);
        var second = Offer(snapshot, secondPolicy, secondRequest);
        Assert.Equal((5480L, 10520L), Bounds(Assert.Single(first.Plan.Operations)));
        Assert.Equal((11_000L, 12_000L), Bounds(Assert.Single(second.Plan.Operations)));
        Assert.NotEqual(first.TimelineKey, second.TimelineKey);
        Assert.Equal("local:v2", second.Plan.Authority.ViewerRef);
        Assert.Equal(secondPolicy.ProfileRef, second.Plan.Authority.ProfileRef);
        Refuses(RefusalCode.AuthorityMismatch, snapshot, secondPolicy);
    }

    [Fact]
    public void RenewalChangesRevisionAndTimesButNotSemantics()
    {
        var revisions = new SequenceRevisions();
        var instance = Offer(Snapshot, Policy, Request, revisions);
        var time = Issued.AddTicks(1);
        var request = Request with { Clock = new SingleReadClock(time), ClockFloor = time, Validity = TimeSpan.FromMinutes(5) };
        var renewal = Offer(Snapshot, Policy, request, revisions);
        Assert.Equal(2, revisions.Count);
        Assert.Equal(instance.TimelineKey, renewal.TimelineKey);
        Assert.NotEqual(instance.Plan.Revision, renewal.Plan.Revision);
        Assert.NotEqual(instance.Plan.IssuedAt, renewal.Plan.IssuedAt);
        Assert.NotEqual(instance.Plan.ExpiresAt, renewal.Plan.ExpiresAt);
        Assert.Equal(time, renewal.Plan.IssuedAt);
        Assert.Equal(time + request.Validity, renewal.Plan.ExpiresAt);
        var first = JsonNode.Parse(instance.Utf8.ToArray())!.AsObject();
        var renewed = JsonNode.Parse(renewal.Utf8.ToArray())!.AsObject();
        foreach (var field in new[] { "revision", "issuedAt", "expiresAt" })
        {
            first.Remove(field);
            renewed.Remove(field);
        }
        Assert.True(JsonNode.DeepEquals(first, renewed));
    }

    [Theory]
    [InlineData("policy", RefusalCode.AuthorityMismatch)]
    [InlineData("trust", RefusalCode.AuthorityMismatch)]
    [InlineData("snapshot", RefusalCode.SnapshotMismatch)]
    public void ChangedProvenanceInvalidatesOldPlanButPreservesSameTimeline(string kind, RefusalCode reason)
    {
        var revisions = new SequenceRevisions();
        var old = Offer(Snapshot, Policy, revisions: revisions);
        var snapshot = kind == "snapshot" ? Snapshot with { SnapshotRev = "home:review:10" } : Snapshot;
        var policy = kind switch
        {
            "policy" => Policy with { PolicyRev = "policy:8" },
            "trust" => Policy with { TrustRev = "trust:3" },
            _ => Policy
        };
        var current = Offer(snapshot, policy, revisions: revisions);
        Assert.NotEqual(old.Plan.Revision, current.Plan.Revision);
        Assert.Equal(policy.PolicyRev, current.Plan.Authority.PolicyRev);
        Assert.Equal(policy.TrustRev, current.Plan.Authority.TrustRev);
        Assert.Equal(snapshot.SnapshotRev, current.Plan.Inputs.SnapshotRev);
        Assert.Equal(old.TimelineKey, current.TimelineKey);
        var facts = Verified(snapshot, policy, Request, current.Plan.Revision);
        Assert.Equal(RefusalCode.RevisionMismatch, Parse(old.Utf8.ToArray(), facts).Reason);
        Assert.Equal(reason, Parse(old.Utf8.ToArray(), facts with { ActiveRevision = old.Plan.Revision }).Reason);
        Assert.True(Parse(current.Utf8.ToArray(), facts).Accepted);
    }

    [Fact]
    public void RefBindsAllSemanticFieldsToTheReviewedDigest()
    {
        var original = Snapshot.Annotations[0];
        foreach (var mutation in new[]
        {
            original with { EndMs = original.EndMs + 1 }, original with { StartMs = original.StartMs + 1 },
            original with { Category = "nudity" }, original with { Severity = 3 }, original with { Action = "cut" },
            original with { Ref = "home:replaced" }, original with { ContentSha256 = new string('b', 64) }
        })
            Refuses(RefusalCode.AnnotationDigestMismatch, Marks(mutation), Policy);

        var revisions = new SequenceRevisions();
        var old = Offer(Snapshot, Policy, revisions: revisions);
        var newlyReviewed = Marks(Annotation(original.Ref, original.StartMs, original.EndMs + 1)) with { SnapshotRev = "home:review:10" };
        var current = Offer(newlyReviewed, Policy, revisions: revisions);
        Assert.NotEqual(old.TimelineKey, current.TimelineKey);
        Assert.NotEqual(old.Plan.Inputs.Annotations[0].ContentSha256, current.Plan.Inputs.Annotations[0].ContentSha256);
        var oldSnapshotLabel = newlyReviewed with { SnapshotRev = Snapshot.SnapshotRev };
        Assert.Equal(RefusalCode.AnnotationDigestMismatch,
            Parse(old.Utf8.ToArray(), Verified(oldSnapshotLabel, Policy, Request, old.Plan.Revision)).Reason);
    }

    [Fact]
    public void EqualSampledEndsLengthAndDurationAreNotAnExactBinding()
    {
        var bytes = new byte[3 * 65_536];
        var binding = Snapshot.Binding with { SizeBytes = bytes.Length, Sha256 = Convert.ToHexStringLower(SHA256.HashData(bytes)) };
        var snapshot = Snapshot with { Binding = binding };
        var request = Request with { SelectedSource = Identity(binding) };
        _ = Offer(snapshot, Policy, request);
        bytes[65_536] = 1;
        var changed = request.SelectedSource with { Sha256 = Convert.ToHexStringLower(SHA256.HashData(bytes)) };
        var noPolicy = (HouseholdPolicy?)null;
        // Even an absent policy or an unusable snapshot must not mask the source mismatch.
        Refuses(RefusalCode.BindingMismatch, snapshot with { Annotations = [] }, noPolicy, request with { SelectedSource = changed });
        Refuses(RefusalCode.BindingMismatch, snapshot, Policy, request with { ServerRef = "another:server" });
        Refuses(RefusalCode.BindingMismatch, snapshot, Policy, request with
        {
            SelectedSource = request.SelectedSource with { MediaSourceId = "another:source" }
        });
    }

    [Fact]
    public void TrackSetChangesTimelineButEnumerationOrderDoesNot()
    {
        var first = Offer(Snapshot, Policy);
        var reordered = Snapshot with { Binding = Snapshot.Binding with { Tracks = Snapshot.Binding.Tracks.Reverse().ToImmutableArray() } };
        Assert.Equal(first.TimelineKey, Offer(reordered, Policy).TimelineKey);
        var anchor = Snapshot.Binding.Tracks[1] with { AnchorPts = -17, AnchorSourceMs = 3 };
        var changed = Snapshot with { Binding = Snapshot.Binding with { Tracks = Snapshot.Binding.Tracks.SetItem(1, anchor) } };
        Assert.NotEqual(first.TimelineKey, Offer(changed, Policy).TimelineKey);
        var revised = Snapshot with { Binding = Snapshot.Binding with { BindingRev = "binding:4" } };
        Assert.NotEqual(first.TimelineKey, Offer(revised, Policy).TimelineKey);
    }

    [Fact]
    public void MergeOverlapTouchAndNestingWithoutLosingRefsOrMovingBoundaries()
    {
        var snapshot = Marks(Annotation("z", 100, 130), Annotation("e", 40, 70, action: "cut"),
            Annotation("a", 3, 40), Annotation("c", 25, 50), Annotation("b", 5, 7), Annotation("d", 69, 75));
        var result = Offer(snapshot, Policy);
        Assert.Collection(result.Plan.Operations,
            cut => { Assert.Equal((3L, 75L), Bounds(cut)); Assert.Equal(["a", "b", "c", "d", "e"], cut.From.ToArray()); },
            cut => { Assert.Equal((100L, 130L), Bounds(cut)); Assert.Equal("z", Assert.Single(cut.From)); });
        Assert.Equal(6, result.Plan.Inputs.Annotations.Length);
        var reverse = Offer(snapshot with { Annotations = snapshot.Annotations.Reverse().ToImmutableArray() }, Policy);
        Assert.Equal(result.TimelineKey, reverse.TimelineKey);
        Assert.Equal(JsonNode.Parse(result.Utf8.ToArray())!["operations"]!.ToJsonString(),
            JsonNode.Parse(reverse.Utf8.ToArray())!["operations"]!.ToJsonString());
    }

    [Fact]
    public void RulesUseInclusiveThresholdsAndDoNotConflateUnselectedAndUnsupportedActions()
    {
        var snapshot = Marks(Annotation("below", 1, 2, severity: 1, action: "mute"),
            Annotation("equal", 3, 4), Annotation("above", 5, 6, severity: 3),
            Annotation("mark", 7, 8, action: "mark", severity: 3),
            Annotation("missing-rule", 9, 10, category: "profanity", action: "mute"),
            Annotation("leave", 11, 12, category: "nudity", severity: 3),
            Annotation("zero", 13, 14, category: "gore", severity: 0));
        var policy = Policy with { Rules = Policy.Rules.Add(new("nudity", 0, false)).Add(new("gore", 0, true)) };
        var plan = Offer(snapshot, policy).Plan;
        Assert.Equal(["equal", "above", "zero"], plan.Operations.Select(cut => Assert.Single(cut.From)).ToArray());
        Assert.Equal(snapshot.Annotations.Length, plan.Inputs.Annotations.Length);
    }

    [Fact]
    public void RequiredMuteAndUnknownInputNeverProduceAPartialPlan()
    {
        Refuses(RefusalCode.RequiredMute, Marks(Snapshot.Annotations[0], Annotation("mute", 6000, 7000, action: "mute")), Policy);
        Refuses(RefusalCode.UnsupportedAction, Marks(Snapshot.Annotations[0],
            Annotation("unsupported", 1, 2, category: "nudity", action: "blur")), Policy);
        Refuses(RefusalCode.UnknownCategory, Marks(Snapshot.Annotations[0], Annotation("bad", 1, 2, category: "other")), Policy);
        Refuses(RefusalCode.UnknownCategory, Snapshot, Policy with { Rules = [new("other", 0, false)] });
        Refuses(RefusalCode.ConflictingInput, Snapshot, Policy with { Rules = Policy.Rules.Add(new("violence", 0, false)) });
        Refuses(RefusalCode.DuplicateReference, Marks(Snapshot.Annotations[0], Annotation(Mark, 2, 3)), Policy);
        Refuses(RefusalCode.InvalidInterval, Marks(Annotation("invalid", 1, Snapshot.Binding.DurationMs + 1)), Policy);
    }

    [Fact]
    public void EmptyAbsentStaleNoCutsAndAllCutAreDistinct()
    {
        Refuses(RefusalCode.EmptySnapshot, Marks(), Policy);
        Refuses(RefusalCode.NoCuts, Marks(Annotation("description", 1, 2, action: "mark")), Policy);
        Refuses(RefusalCode.NoKeptRanges, Marks(Annotation("start", 0, 10), Annotation("end", 10, Snapshot.Binding.DurationMs)), Policy);
        Refuses(RefusalCode.AbsentSnapshot, null, Policy);
        Refuses(RefusalCode.StaleSnapshot, Snapshot with { ExpiresAt = Issued, AcceptedAt = Issued.AddHours(-1) }, Policy);
        Refuses(RefusalCode.StaleSnapshot, Snapshot with { AcceptedAt = Issued.AddTicks(1) }, Policy);
    }

    [Fact]
    public void RefusesClockRollbackAndValidityOutsideTheSnapshotOrSliceMaximum()
    {
        Refuses(RefusalCode.ClockRollback, Snapshot, Policy, Request with { ClockFloor = Issued.AddTicks(1) });
        Refuses(RefusalCode.InvalidValidityWindow, Snapshot, Policy, Request with { Validity = Request.Validity.Add(TimeSpan.FromTicks(1)) });
        Refuses(RefusalCode.InvalidValidityWindow, Snapshot, Policy, Request with { Validity = TimeSpan.Zero });
        Refuses(RefusalCode.InvalidValidityWindow, Snapshot with { ExpiresAt = Issued.AddMinutes(29) }, Policy);
        Assert.Equal(Expires, Offer(Snapshot with { ExpiresAt = Expires }, Policy).Plan.ExpiresAt);
    }

    [Fact]
    public void RandomValidMarksAndKeptRangesPartitionTheSourceExactly()
    {
        var random = new Random(824107);
        var revisions = new SequenceRevisions();
        for (int trial = 0; trial < 200; trial++)
        {
            int duration = random.Next(3, 200);
            var source = Snapshot.Binding with { DurationMs = duration };
            var annotations = Enumerable.Range(0, random.Next(1, 20)).Select(i =>
            {
                var start = random.Next(1, duration); // Retain source instant zero in these positive vectors.
                return Annotation(FormattableString.Invariant($"mark:{i}"), start, random.Next(start + 1, duration + 1));
            }).ToArray();
            var snapshot = Marks(annotations) with { Binding = source };
            var request = Request with { SelectedSource = Identity(source) };
            var result = Offer(snapshot, Policy, request, revisions);
            var plan = Require(Parse(result.Utf8.ToArray(), Verified(snapshot, Policy, request, result.Plan.Revision)));
            var filtered = 0;
            for (int ms = 0; ms < duration; ms++)
            {
                bool marked = annotations.Any(mark => ms >= mark.StartMs && ms < mark.EndMs);
                Assert.Equal(marked ? 1 : 0, plan.Operations.Count(cut => ms >= cut.StartMs && ms < cut.EndMs));
                Assert.Equal(marked ? 0 : 1, plan.Timeline.KeptRanges.Count(kept => ms >= kept.SourceStartMs && ms < kept.SourceEndMs));
                if (!marked)
                {
                    Assert.Equal(filtered, plan.Timeline.SourceToFiltered(ms));
                    Assert.Equal(ms, plan.Timeline.FilteredToSource(filtered++));
                }
            }
            Assert.Equal(filtered, plan.Timeline.FilteredDurationMs);
            Assert.Equal(duration, filtered + plan.Operations.Sum(cut => cut.EndMs - cut.StartMs));
        }
    }

    [Theory]
    [InlineData(true, "/snapshotVersion", "2", RefusalCode.UnsupportedVersion)]
    [InlineData(true, "/coverage", "\"complete\"", RefusalCode.InvalidValue)]
    [InlineData(true, "/annotations/0/action", "\"crop\"", RefusalCode.UnsupportedAction)]
    [InlineData(true, "/annotations/0/category", "\"other\"", RefusalCode.UnknownCategory)]
    [InlineData(true, "/annotations/0/severity", "4", RefusalCode.InvalidValue)]
    [InlineData(true, "/annotations/0/startMs", "5480.5", RefusalCode.InvalidValue)]
    [InlineData(true, "/annotations/0/contentSha256", "\"bad-digest\"", RefusalCode.InvalidDigest)]
    [InlineData(true, "/annotations/0/endMs", null, RefusalCode.MissingField)]
    [InlineData(true, "/annotations/0/status", "\"published\"", RefusalCode.UnknownField)]
    [InlineData(true, "/binding/tracks/0/timeBase", "[0,12800]", RefusalCode.InvalidValue)]
    [InlineData(true, "/binding/tracks/0/approximate", "true", RefusalCode.UnknownField)]
    [InlineData(true, "/acceptedAt", "\"2026-11-21T18:00:00+00:00\"", RefusalCode.InvalidTimestamp)]
    [InlineData(false, "/policyVersion", "2", RefusalCode.UnsupportedVersion)]
    [InlineData(false, "/viewerRef", "\"local:someone\"", RefusalCode.UnknownField)]
    [InlineData(false, "/profileRef", null, RefusalCode.MissingField)]
    [InlineData(false, "/rules/0/category", "\"Violence\"", RefusalCode.UnknownCategory)]
    [InlineData(false, "/rules/0/filter", "\"yes\"", RefusalCode.InvalidValue)]
    [InlineData(false, "/rules/0/minimumSeverity", "-1", RefusalCode.InvalidValue)]
    [InlineData(false, "/rules/0/actionOverride", "\"cut\"", RefusalCode.UnknownField)]
    public void SchemaAndStrictReadersAgreeOnStructuralRefusals(bool snapshot, string path, string? value, RefusalCode reason)
    {
        var json = JsonNode.Parse(Fixture(snapshot ? SnapshotFile : PolicyFile))!;
        var components = path[1..].Split('/');
        foreach (var component in components[..^1])
            json = json is JsonArray ? json[int.Parse(component, CultureInfo.InvariantCulture)]! : json[component]!;
        if (value is null)
            json.AsObject().Remove(components[^1]);
        else
            json[components[^1]] = JsonNode.Parse(value);
        var bytes = Bytes(json.Root);
        Assert.False(SchemaAccepts(snapshot ? SnapshotSchema : PolicySchema, bytes));
        Assert.Equal(reason, snapshot ? ResolverInputReader.ParseSnapshot(bytes).Reason : ResolverInputReader.ParsePolicy(bytes).Reason);
    }

    [Fact]
    public void ReaderEnforcesByteLevelAndSemanticRulesBeyondTheSchema()
    {
        var snapshot = Encoding.UTF8.GetString(Fixture(SnapshotFile));
        var policy = Encoding.UTF8.GetString(Fixture(PolicyFile));
        Assert.Equal(RefusalCode.DuplicateKey, ResolverInputReader.ParseSnapshot(Encoding.UTF8.GetBytes(snapshot.Replace(
            "\"severity\": 2", "\"severity\": 2, \"severit\\u0079\": 2"))).Reason);
        Assert.Equal(RefusalCode.DuplicateKey, ResolverInputReader.ParsePolicy(Encoding.UTF8.GetBytes(policy.Replace(
            "\"filter\": true", "\"filter\": true, \"filter\": false"))).Reason);
        foreach (var spelling in new[] { "5480.0", "5.48e3" })
            Assert.Equal(RefusalCode.InvalidValue, ResolverInputReader.ParseSnapshot(Encoding.UTF8.GetBytes(
                snapshot.Replace("5480", spelling))).Reason);
        Assert.Equal(RefusalCode.AnnotationDigestMismatch,
            ResolverInputReader.ParseSnapshot(Encoding.UTF8.GetBytes(snapshot.Replace("5480", "5481"))).Reason);
        Assert.Equal(RefusalCode.InvalidJson, ResolverInputReader.ParseSnapshot(new byte[] { 0xff }).Reason);
        Assert.Equal(RefusalCode.AbsentSnapshot, ResolverInputReader.ParseSnapshot(ReadOnlyMemory<byte>.Empty).Reason);
        Assert.Equal(RefusalCode.LimitExceeded, ResolverInputReader.ParsePolicy(new byte[ResolverInputReader.MaxDocumentBytes + 1]).Reason);
    }

    private static bool SchemaAccepts(string schema, byte[] json) => JsonSchema.FromText(File.ReadAllText(
        Path.Combine(AppContext.BaseDirectory, schema))).Evaluate(JsonNode.Parse(json),
        new EvaluationOptions { RequireFormatValidation = true }).IsValid;

    private static AcceptedAnnotation Annotation(string reference, long start, long end, string category = "violence",
        int severity = 2, string action = "skip")
    {
        var mark = new AcceptedAnnotation(reference, "", category, severity, action, start, end);
        return mark with { ContentSha256 = mark.ComputeContentSha256() };
    }

    private static AcceptedAnnotationSnapshot Marks(params AcceptedAnnotation[] marks) =>
        Snapshot with { Annotations = marks.ToImmutableArray() };

    private static ResolvedPlaybackPlan Offer(AcceptedAnnotationSnapshot snapshot, HouseholdPolicy policy,
        ResolverRequest? request = null, SequenceRevisions? revisions = null)
    {
        var result = HouseholdResolver.Resolve(snapshot, policy, request ?? Request, revisions ?? Revisions);
        Assert.True(result.Accepted, result.Reason?.ToString());
        return Assert.IsType<ResolvedPlaybackPlan>(result.Value);
    }

    private static void Refuses(RefusalCode reason, AcceptedAnnotationSnapshot? snapshot, HouseholdPolicy? policy,
        ResolverRequest? request = null)
    {
        var revisions = new SequenceRevisions();
        var result = HouseholdResolver.Resolve(snapshot, policy, request ?? Request, revisions);
        Assert.Equal(reason, result.Reason);
        Assert.Null(result.Value);
        Assert.Equal(0, revisions.Count);
    }

    private static VerifiedPlanContext Verified(AcceptedAnnotationSnapshot snapshot, HouseholdPolicy policy,
        ResolverRequest request, string revision) => new(revision, snapshot.Binding,
        new(request.ViewerRef, request.ProfileRef, policy.PolicyRev, policy.TrustRev), snapshot.SnapshotRev, snapshot.Coverage,
        snapshot.Annotations.ToImmutableDictionary(mark => mark.Ref, mark => mark.ContentSha256), request.ClockFloor);

    private static VerifiedSourceIdentity Identity(SourceBinding source) => new(source.ItemId, source.MediaSourceId,
        source.Sha256, source.SizeBytes, source.DurationMs);

    private static (long, long) Bounds(CutOperation cut) => (cut.StartMs, cut.EndMs);

    private sealed class SequenceRevisions : IPlanRevisionAllocator
    {
        public int Count { get; private set; }
        public string AllocateRevision() => FormattableString.Invariant($"local:resolved:{++Count}");
    }

    private sealed class FixedClock(DateTimeOffset time) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => time;
    }

    private sealed class SingleReadClock(DateTimeOffset time) : TimeProvider
    {
        private int reads;
        public override DateTimeOffset GetUtcNow()
        {
            Assert.Equal(1, ++reads);
            return time;
        }
    }
}
