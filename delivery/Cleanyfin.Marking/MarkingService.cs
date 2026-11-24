using System.Collections.Immutable;
using System.Security.Cryptography;
using Cleanyfin.Delivery;

namespace Cleanyfin.Marking;

/// <summary>
/// Capture → proposed mark → refined mark → recorded acceptance → annotation-snapshot-v1. Pure: no HTTP, no media.
/// Every decision is an audit event naming actor, subject and digest. Nothing here guesses a boundary, accepts on
/// anyone's behalf, or carries a mark to another file.
/// </summary>
public sealed class MarkingService(IMarkingStore store, Governance governance, Taxonomy taxonomy, TimeProvider clock,
    IIssuedPlanLookup? plans = null)
{
    public const string HouseholdLicense = "household-private";
    public const string Proposed = "proposed", Accepted = "accepted", Rejected = "rejected", Withdrawn = "withdrawn";

    public IMarkingStore Store => store;

    /// <summary>
    /// Records one press. Any household member may add captures (a viewer's "flag"). A suggester (household compute
    /// service) may only add captures from the <c>service</c> surface, and only suggesters may use that surface.
    /// </summary>
    public MarkingResult<Capture> AddCapture(Capture capture) => Run(() =>
    {
        Require(governance.IsMember(capture.Author), MarkingRefusal.UnknownMember);
        Require(governance.IsSuggester(capture.Author) == (capture.Surface == "service"), MarkingRefusal.NotPermitted);
        var check = MarkingDocuments.ParseCapture(MarkingDocuments.WriteCapture(capture));
        Require(check.Accepted, check.Reason ?? MarkingRefusal.InvalidValue);
        store.AppendCapture(capture);
        store.AppendAudit(clock.GetUtcNow(), capture.Author, AuditTypes.CaptureAdd, capture.Ref, null);
        return capture;
    });

    /// <summary>Source-clock position of a capture, through the issued plan's kept ranges when it was on a filtered clock.</summary>
    public static MarkingResult<SourcePosition> FilteredToSource(Capture capture, IIssuedPlanLookup? plans) => Run(() =>
    {
        if (capture.Clock.Kind == "source")
            return new SourcePosition(capture.AtMs, capture.EndAtMs);
        Require(capture.Clock.Kind == "filtered" && capture.Clock.PlanRev is not null, MarkingRefusal.InvalidValue);
        // An unknown plan revision leaves the capture unmapped, for manual placement; nothing is guessed.
        var plan = plans?.FindIssuedPlan(capture.Clock.PlanRev!) ?? throw new MarkingRejected(MarkingRefusal.UnknownPlan);
        Require(plan.Source.ItemId == capture.Target.ItemId && plan.Source.MediaSourceId == capture.Target.MediaSourceId &&
            plan.Source.ServerRef == capture.Target.ServerRef, MarkingRefusal.BindingMismatch);
        var timeline = plan.Timeline;
        Require(capture.AtMs <= timeline.FilteredDurationMs && (capture.EndAtMs ?? 0) <= timeline.FilteredDurationMs,
            MarkingRefusal.OutsideTimeline);
        Require(!timeline.KeptRanges.IsEmpty, MarkingRefusal.OutsideTimeline);
        return new SourcePosition(timeline.FilteredToSource(capture.AtMs),
            capture.EndAtMs is { } end ? timeline.FilteredToSource(end) : null);
    });

    /// <summary>
    /// A proposed mark whose bounds are only the capture's search window (<c>boundsSource: capture-window</c>). It has no
    /// category, severity or action yet and cannot be accepted until a person sets exact bounds.
    /// </summary>
    public MarkingResult<Mark> CaptureToDraftMark(string captureRef, string markRef, string bindingRev, string actor) => Run(() =>
    {
        Require(governance.MayMark(actor), MarkingRefusal.NotPermitted);
        var capture = store.FindCapture(captureRef) ?? throw new MarkingRejected(MarkingRefusal.UnknownCapture);
        var position = FilteredToSource(capture, plans);
        if (!position.Accepted)
            throw new MarkingRejected(position.Reason!.Value);
        var at = position.Value!;
        var start = Math.Max(0, at.AtMs - capture.Window.BeforeMs);
        var end = (at.EndAtMs ?? at.AtMs) + capture.Window.AfterMs;
        if (end <= start)
            end = start + 1;
        var method = capture.Surface == "service" ? "auto:service" : "live";
        return Create(new Mark(markRef, 1, bindingRev, start, end, Mark.CaptureWindowBounds, null, null, null, null, Proposed,
            new([capture.Ref], method, actor, HouseholdLicense), actor, null));
    });

    /// <summary>
    /// A reviewer places exact bounds for a capture directly. This is how an unmapped capture (an unknown filtered plan)
    /// still becomes a mark: by a person, on the source clock.
    /// </summary>
    public MarkingResult<Mark> PlaceFromCapture(string captureRef, string markRef, string bindingRev, MarkEdit exact, string actor) => Run(() =>
    {
        Require(governance.MayMark(actor), MarkingRefusal.NotPermitted);
        var capture = store.FindCapture(captureRef) ?? throw new MarkingRejected(MarkingRefusal.UnknownCapture);
        var (category, subcategory) = Classify(exact, previousSubcategory: null);
        return Create(WithDigest(new Mark(markRef, 1, bindingRev, exact.StartMs, exact.EndMs, Mark.ReviewerBounds, category,
            subcategory, exact.Severity, exact.Action, Proposed, new([capture.Ref], "refined", actor, HouseholdLicense), actor, null)));
    });

    /// <summary>
    /// Sets exact bounds and classification as a new revision in <c>proposed</c>. Editing an accepted mark therefore
    /// drops it from the next snapshot until that new revision is accepted. Markers may edit only their own proposed marks.
    /// </summary>
    public MarkingResult<Mark> Edit(string markRef, int expectedRev, MarkEdit edit, string actor) => Run(() =>
    {
        var latest = store.FindMark(markRef) ?? throw new MarkingRejected(MarkingRefusal.UnknownMark);
        Require(latest.Rev == expectedRev, MarkingRefusal.StaleRevision);
        Require(governance.MayReview(actor) ||
            (governance.MayMark(actor) && latest.Provenance.By == actor && latest.State == Proposed), MarkingRefusal.NotPermitted);
        var (category, subcategory) = Classify(edit, latest.Subcategory);
        var next = WithDigest(latest with
        {
            Rev = latest.Rev + 1, StartMs = edit.StartMs, EndMs = edit.EndMs, BoundsSource = Mark.ReviewerBounds,
            Category = category, Subcategory = subcategory, Severity = edit.Severity, Action = edit.Action, State = Proposed,
            Provenance = latest.Provenance with { Method = "refined" }, EditedBy = actor
        });
        store.PutMark(next);
        store.AppendAudit(clock.GetUtcNow(), actor, AuditTypes.MarkEdit, Subject(next), next.ContentSha256);
        return next;
    });

    /// <summary>
    /// Accepts exactly <paramref name="markRef"/>@<paramref name="rev"/>. Reviewers only; the same person may mark and
    /// approve unless the household requires a second reviewer (R17). The audit event binds the digest.
    /// </summary>
    public MarkingResult<AuditEvent> Accept(string markRef, int rev, string actor) => Run(() =>
    {
        var mark = Decide(markRef, rev, actor);
        Require(mark.IsComplete, MarkingRefusal.IncompleteMark);
        Require(mark.BoundsSource == Mark.ReviewerBounds, MarkingRefusal.BoundsNotRefined);
        Require(mark.ContentSha256 == mark.ComputeContentSha256(), MarkingRefusal.DigestMismatch);
        Require(governance.Approval.SelfApproval || (mark.Provenance.By != actor && mark.EditedBy != actor),
            MarkingRefusal.SecondReviewerRequired);
        store.PutMark(mark with { State = Accepted });
        return store.AppendAudit(clock.GetUtcNow(), actor, AuditTypes.MarkAccept, Subject(mark), mark.ContentSha256);
    });

    public MarkingResult<AuditEvent> Reject(string markRef, int rev, string actor) => Run(() =>
    {
        var mark = Decide(markRef, rev, actor);
        store.PutMark(mark with { State = Rejected });
        return store.AppendAudit(clock.GetUtcNow(), actor, AuditTypes.MarkReject, Subject(mark), mark.ContentSha256);
    });

    /// <summary>Removes a mark from future snapshots. Reviewers only; the revision stays in the record.</summary>
    public MarkingResult<AuditEvent> Withdraw(string markRef, int rev, string actor) => Run(() =>
    {
        Require(governance.MayReview(actor), MarkingRefusal.NotPermitted);
        var mark = store.FindMark(markRef) ?? throw new MarkingRejected(MarkingRefusal.UnknownMark);
        Require(mark.Rev == rev, MarkingRefusal.StaleRevision);
        Require(mark.State is Proposed or Accepted, MarkingRefusal.NotProposed);
        store.PutMark(mark with { State = Withdrawn });
        return store.AppendAudit(clock.GetUtcNow(), actor, AuditTypes.MarkWithdraw, Subject(mark), mark.ContentSha256);
    });

    /// <summary>
    /// Accepted marks for one binding → annotation-snapshot-v1 bytes, validated by the existing strict reader. A mark is
    /// included only if its latest revision is accepted and the verified audit log holds a <c>mark.accept</c> for that
    /// exact ref@rev and digest. Reviewers only. <paramref name="expiresAt"/> defaults to the household's expiry and may
    /// not exceed it.
    /// </summary>
    /// <summary>
    /// Open (proposed or accepted) marks on the same item that were made on a different edition than
    /// <paramref name="current"/>: the "file changed, review again" list. Marks are never moved to the new edition.
    /// </summary>
    public IEnumerable<Mark> StaleMarks(SourceBinding current)
    {
        // A mark belongs to an item through its binding (plugin form "bind:<item>:<edition>") or the captures it came from.
        var prefix = "bind:" + current.ItemId + ":";
        bool SameItem(Mark m) => m.BindingRev.StartsWith(prefix, StringComparison.Ordinal) ||
            m.Provenance.From.Any(r => store.FindCapture(r)?.Target.ItemId == current.ItemId);
        return store.LatestMarks().Where(m => m.BindingRev != current.BindingRev && m.State is Proposed or Accepted && SameItem(m));
    }

    public MarkingResult<PublishedSnapshot> Publish(SourceBinding binding, string actor, string snapshotRev,
        DateTimeOffset? expiresAt = null) => Run(() =>
    {
        Require(governance.MayReview(actor), MarkingRefusal.NotPermitted);
        Require(StrictJson.IsReference(snapshotRev), MarkingRefusal.InvalidValue);
        var log = AuditLog.Verify(store.AuditLines());
        if (!log.Accepted)
            throw new MarkingRejected(MarkingRefusal.AuditChainBroken, line: log.Line);
        var acceptances = log.Value!.Where(e => e.Type == AuditTypes.MarkAccept)
            .Select(e => (e.Subject, e.Digest)).ToHashSet();
        var now = clock.GetUtcNow();
        var limit = now.AddDays(governance.Approval.SnapshotExpiryDays);
        var expires = expiresAt ?? limit;
        Require(expires > now && expires <= limit, MarkingRefusal.InvalidExpiry);
        // A file that changed since some of its open marks were made is not published at all: not with the old marks
        // (they describe other bytes) and not without them (that would silently drop reviewed filtering). The
        // reviewer withdraws or rejects each "file changed" mark, and re-marks the new file, first.
        Require(!StaleMarks(binding).Any(), MarkingRefusal.FileChanged);
        var annotations = ImmutableArray.CreateBuilder<AcceptedAnnotation>();
        foreach (var mark in store.LatestMarks().Where(m => m.BindingRev == binding.BindingRev && m.State == Accepted)
                     .OrderBy(m => m.StartMs).ThenBy(m => m.Ref, StringComparer.Ordinal))
        {
            Require(acceptances.Contains((Subject(mark), mark.ContentSha256)), MarkingRefusal.NoRecordedAcceptance);
            annotations.Add(mark.ToAnnotation());
        }
        var snapshot = new AcceptedAnnotationSnapshot(snapshotRev, PlanValidator.ReviewedCoverage, now, expires, binding,
            annotations.ToImmutable());
        var bytes = MarkingDocuments.WriteSnapshot(snapshot);
        var reread = ResolverInputReader.ParseSnapshot(bytes);
        if (!reread.Accepted)
            throw new MarkingRejected(MarkingRefusal.SnapshotInvalid, reread.Reason);
        var e = store.AppendAudit(now, actor, AuditTypes.SnapshotPublish, snapshotRev,
            Convert.ToHexStringLower(SHA256.HashData(bytes)));
        return new PublishedSnapshot(bytes, reread.Value!, e);
    });

    /// <summary>
    /// Governance floor + a viewer's own preferences → the existing household-policy-v1, stricter wins: a category is
    /// filtered if the floor or the viewer filters it, at the lower minimum severity. A preference can never loosen the
    /// floor; under <c>viewerMay: none</c> preferences are ignored. policyRev changes whenever either revision changes.
    /// </summary>
    public static MarkingResult<CompiledPolicy> CompilePolicy(Governance governance, string profileRef, HouseholdPolicy? viewerPrefs) => Run(() =>
    {
        var profile = governance.Profiles.FirstOrDefault(p => p.ProfileRef == profileRef)
            ?? throw new MarkingRejected(MarkingRefusal.UnknownProfile);
        Require(viewerPrefs is null || viewerPrefs.ProfileRef == profileRef, MarkingRefusal.ProfileMismatch);
        var prefs = profile.ViewerMay == "tighten-only" ? viewerPrefs : null;
        if (prefs is not null)
        {
            Require(!prefs.Rules.IsDefault && prefs.Rules.Length <= CoreCategories.All.Length, MarkingRefusal.LimitExceeded);
            Require(prefs.Rules.All(r => CoreCategories.IsCore(r.Category)), MarkingRefusal.UnknownCategory);
            Require(prefs.Rules.All(r => r.MinimumSeverity is >= 0 and <= 3), MarkingRefusal.InvalidValue);
            Require(prefs.Rules.Select(r => r.Category).Distinct().Count() == prefs.Rules.Length, MarkingRefusal.ConflictingInput);
        }
        var rules = ImmutableArray.CreateBuilder<CategoryRule>();
        foreach (var category in CoreCategories.All)
        {
            var floor = profile.Floor.FirstOrDefault(r => r.Category == category && r.Filter);
            var own = prefs?.Rules.FirstOrDefault(r => r.Category == category && r.Filter);
            if (floor is null && own is null)
                continue;
            var minimum = Math.Min(floor?.MinimumSeverity ?? 3, own?.MinimumSeverity ?? 3);
            rules.Add(new CategoryRule(category, minimum, true));
        }
        var policyRev = $"compiled:{governance.Rev}:{(prefs is null ? "none" : prefs.PolicyRev)}";
        Require(StrictJson.IsReference(policyRev), MarkingRefusal.LimitExceeded);
        var policy = new HouseholdPolicy(profileRef, policyRev, governance.Rev, rules.ToImmutable());
        var bytes = MarkingDocuments.WritePolicy(policy);
        var reread = ResolverInputReader.ParsePolicy(bytes);
        if (!reread.Accepted)
            throw new MarkingRejected(MarkingRefusal.SnapshotInvalid, reread.Reason);
        return new CompiledPolicy(bytes, reread.Value!);
    });

    public static string Subject(Mark mark) => FormattableString.Invariant($"{mark.Ref}@{mark.Rev}");

    private Mark Decide(string markRef, int rev, string actor)
    {
        Require(governance.MayReview(actor), MarkingRefusal.NotPermitted);
        var mark = store.FindMark(markRef) ?? throw new MarkingRejected(MarkingRefusal.UnknownMark);
        Require(mark.Rev == rev, MarkingRefusal.StaleRevision);
        Require(mark.State == Proposed, MarkingRefusal.NotProposed);
        return mark;
    }

    private Mark Create(Mark mark)
    {
        Require(StrictJson.IsReference(mark.Ref) && StrictJson.IsReference(mark.BindingRev), MarkingRefusal.InvalidValue);
        Require(store.FindMark(mark.Ref) is null, MarkingRefusal.DuplicateReference);
        store.PutMark(mark);
        store.AppendAudit(clock.GetUtcNow(), mark.EditedBy, AuditTypes.MarkCreate, Subject(mark), mark.ContentSha256);
        return mark;
    }

    private (string Category, string? Subcategory) Classify(MarkEdit edit, string? previousSubcategory)
    {
        Require(edit.StartMs >= 0 && edit.StartMs < edit.EndMs, MarkingRefusal.InvalidInterval);
        Require(edit.Severity is >= 0 and <= 3, MarkingRefusal.InvalidValue);
        Require(edit.Action is "skip" or "cut" or "mute" or "mark", MarkingRefusal.InvalidValue);
        string? core = edit.Category;
        Require(core is null || CoreCategories.IsCore(core), MarkingRefusal.UnknownCategory);
        if (edit.Subcategory is { } sub)
        {
            Require(StrictJson.IsConceptId(sub), MarkingRefusal.UnknownConcept);
            // A retired concept stays resolvable for old revisions but is not used for new labels.
            Require(sub == previousSubcategory || !taxonomy.IsDeprecated(sub), MarkingRefusal.ConceptDeprecated);
            var rolled = taxonomy.RollUp(sub);
            Require(rolled.Accepted, rolled.Reason ?? MarkingRefusal.UnknownConcept);
            Require(core is null || core == rolled.Value, MarkingRefusal.SubcategoryMismatch);
            core = rolled.Value;
        }
        return (core ?? throw new MarkingRejected(MarkingRefusal.IncompleteMark), edit.Subcategory);
    }

    private static Mark WithDigest(Mark mark) => mark with { ContentSha256 = mark.ComputeContentSha256() };

    private static void Require(bool ok, MarkingRefusal reason)
    {
        if (!ok)
            throw new MarkingRejected(reason);
    }

    private static MarkingResult<T> Run<T>(Func<T> body) where T : class
    {
        try
        {
            return MarkingResult<T>.Allow(body());
        }
        catch (MarkingRejected e)
        {
            return MarkingResult<T>.Refuse(e.Reason, e.DeliveryReason, e.Line);
        }
    }
}
