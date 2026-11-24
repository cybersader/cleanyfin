using System.Collections.Immutable;
using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using static Cleanyfin.Delivery.PlanValidator;

namespace Cleanyfin.Delivery;

/// <summary>Resolves only current household-approved inputs. It never returns an original or partial offer.</summary>
public static class HouseholdResolver
{
    public static TimeSpan MaximumValidity { get; } = TimeSpan.FromMinutes(30);

    public static Decision<ResolvedPlaybackPlan> Resolve(AcceptedAnnotationSnapshot? snapshot,
        HouseholdPolicy? policy, ResolverRequest request, IPlanRevisionAllocator revisions)
    {
        ArgumentNullException.ThrowIfNull(request);
        ArgumentNullException.ThrowIfNull(revisions);
        if (snapshot is null)
            return Decision<ResolvedPlaybackPlan>.Refuse(RefusalCode.AbsentSnapshot);

        try
        {
            var binding = snapshot.Binding;
            var selected = request.SelectedSource;
            Check(binding is not null && selected is not null, RefusalCode.UnverifiedBinding);
            Check(binding!.ServerRef == request.ServerRef && binding.ItemId == selected!.ItemId &&
                binding.MediaSourceId == selected.MediaSourceId && binding.Sha256 == selected.Sha256 &&
                binding.SizeBytes == selected.SizeBytes && binding.DurationMs == selected.DurationMs,
                RefusalCode.BindingMismatch);
            CheckSource(binding);

            Check(policy is not null && policy.ProfileRef == request.ProfileRef &&
                IsReference(request.ViewerRef) && IsReference(request.ProfileRef), RefusalCode.AuthorityMismatch);
            var rules = ResolverInputValidation.ReadRules(policy!);
            var now = request.Clock.GetUtcNow().ToUniversalTime();
            Check(now >= request.ClockFloor, RefusalCode.ClockRollback);
            ResolverInputValidation.CheckSnapshot(snapshot);
            Check(now >= snapshot.AcceptedAt && now < snapshot.ExpiresAt, RefusalCode.StaleSnapshot);
            Check(request.Validity > TimeSpan.Zero && request.Validity <= MaximumValidity &&
                request.Validity <= snapshot.ExpiresAt - now, RefusalCode.InvalidValidityWindow);
            var expires = now + request.Validity;

            Check(!snapshot.Annotations.IsEmpty, RefusalCode.EmptySnapshot);
            var selectedCuts = new List<AcceptedAnnotation>();
            foreach (var mark in snapshot.Annotations)
            {
                if (!rules.TryGetValue(mark.Category, out var rule) || !rule.Filter ||
                    mark.Severity < rule.MinimumSeverity || mark.Action == "mark")
                    continue;
                Check(mark.Action != "mute", RefusalCode.RequiredMute);
                selectedCuts.Add(mark);
            }
            Check(selectedCuts.Count > 0, RefusalCode.NoCuts);
            var cuts = Normalize(selectedCuts);
            Check(cuts.Length != 1 || cuts[0].StartMs != 0 || cuts[0].EndMs != binding.DurationMs,
                RefusalCode.NoKeptRanges);

            var authority = new PlanAuthority(request.ViewerRef, request.ProfileRef, policy!.PolicyRev, policy.TrustRev);
            var inputs = new PlanInputs(snapshot.SnapshotRev, snapshot.Coverage, snapshot.Annotations
                .OrderBy(mark => mark.Ref, StringComparer.Ordinal)
                .Select(mark => new AnnotationInput(mark.Ref, mark.ContentSha256)).ToImmutableArray());
            var revision = revisions.AllocateRevision();
            Check(IsReference(revision), RefusalCode.InvalidValue);
            var bytes = SerializePlan(revision, now, expires, binding, authority, inputs, cuts);
            var verified = new VerifiedPlanContext(revision, binding, authority, inputs.SnapshotRev, inputs.Coverage,
                inputs.Annotations.ToImmutableDictionary(mark => mark.Ref, mark => mark.ContentSha256,
                    StringComparer.Ordinal), request.ClockFloor);
            // Sample the request clock once; use that instant for both issuance and validation.
            var validated = Parse(bytes, new IssuanceClock(now), verified);
            if (validated.Value is not { } plan)
                return Decision<ResolvedPlaybackPlan>.Refuse(validated.Reason!.Value);
            return Decision<ResolvedPlaybackPlan>.Allow(new(plan, bytes.ToImmutableArray(), TimelineKey(plan)));
        }
        catch (Rejected e)
        {
            return Decision<ResolvedPlaybackPlan>.Refuse(e.Reason);
        }
        catch (OverflowException)
        {
            return Decision<ResolvedPlaybackPlan>.Refuse(RefusalCode.IntegerOverflow);
        }
    }

    private static ImmutableArray<CutOperation> Normalize(List<AcceptedAnnotation> selected)
    {
        selected.Sort(static (a, b) => a.StartMs.CompareTo(b.StartMs));
        var cuts = ImmutableArray.CreateBuilder<CutOperation>();
        var refs = new List<string>();
        long start = selected[0].StartMs, end = selected[0].EndMs;
        foreach (var mark in selected)
        {
            if (mark.StartMs > end)
            {
                AddCut();
                start = mark.StartMs;
                end = mark.EndMs;
            }
            end = Math.Max(end, mark.EndMs);
            refs.Add(mark.Ref);
        }
        AddCut();
        return cuts.ToImmutable();

        void AddCut()
        {
            Check(cuts.Count < MaxOperations, RefusalCode.LimitExceeded);
            refs.Sort(StringComparer.Ordinal);
            cuts.Add(new CutOperation(start, end, refs.ToImmutableArray()));
            refs.Clear();
        }
    }

    private static readonly JsonSerializerOptions WireOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

    private static byte[] SerializePlan(string revision, DateTimeOffset issued, DateTimeOffset expires,
        SourceBinding binding, PlanAuthority authority, PlanInputs inputs, ImmutableArray<CutOperation> cuts) =>
        JsonSerializer.SerializeToUtf8Bytes(new
        {
            planVersion = 1, requires = new[] { Cut }, revision,
            issuedAt = WireTimestamp(issued), expiresAt = WireTimestamp(expires),
            source = WireSource(binding, binding.Tracks), authority, inputs,
            operations = cuts.Select(cut => new { action = Cut, cut.StartMs, cut.EndMs, cut.From })
        }, WireOptions);

    private static string TimelineKey(PlaybackPlan plan)
    {
        var bytes = JsonSerializer.SerializeToUtf8Bytes(new
        {
            source = WireSource(plan.Source, plan.Source.Tracks.OrderBy(track => track.Index)),
            cuts = plan.Operations.Select(cut => new[] { cut.StartMs, cut.EndMs })
        }, WireOptions);
        return "timeline:v1:" + Convert.ToHexStringLower(SHA256.HashData(bytes));
    }

    private static object WireSource(SourceBinding binding, IEnumerable<TrackClock> tracks) => new
    {
        binding.ServerRef, binding.ItemId, binding.MediaSourceId, binding.Sha256, binding.SizeBytes, binding.TimelineRef,
        binding.BindingRev, binding.DurationMs, binding.Origin,
        tracks = tracks.Select(track => new
        {
            track.Index, track.Kind, timeBase = new[] { track.TimeBaseNumerator, track.TimeBaseDenominator },
            track.AnchorPts, track.AnchorSourceMs
        })
    };

    private static string WireTimestamp(DateTimeOffset time) =>
        time.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss.FFFFFFF'Z'", CultureInfo.InvariantCulture);

    private sealed class IssuanceClock(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }
}
