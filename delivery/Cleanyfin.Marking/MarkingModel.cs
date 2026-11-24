using System.Collections.Immutable;
using Cleanyfin.Delivery;

namespace Cleanyfin.Marking;

// Values and names are stable. Refusals never echo document contents.
public enum MarkingRefusal
{
    InvalidJson = 1,
    DuplicateKey = 2,
    UnknownField = 3,
    MissingField = 4,
    InvalidValue = 5,
    UnsupportedVersion = 6,
    LimitExceeded = 7,
    InvalidTimestamp = 8,
    InvalidInterval = 9,
    DuplicateReference = 10,
    UnknownCategory = 11,
    UnknownConcept = 12,
    ConceptCycle = 13,
    ConceptTooDeep = 14,
    ConceptDeprecated = 15,
    SubcategoryMismatch = 16,
    UnknownMember = 17,
    NotPermitted = 18,
    SecondReviewerRequired = 19,
    UnknownCapture = 20,
    UnknownMark = 21,
    StaleRevision = 22,
    NotProposed = 23,
    IncompleteMark = 24,
    BoundsNotRefined = 25,
    DigestMismatch = 26,
    BindingMismatch = 27,
    UnknownPlan = 28,
    OutsideTimeline = 29,
    UnknownProfile = 30,
    ProfileMismatch = 31,
    AuditChainBroken = 32,
    NoRecordedAcceptance = 33,
    SnapshotInvalid = 34,
    InvalidExpiry = 35,
    ConflictingInput = 36
}

public sealed class MarkingResult<T> where T : class
{
    private MarkingResult(T? value, MarkingRefusal? reason, RefusalCode? delivery, int? line)
        => (Value, Reason, DeliveryReason, Line) = (value, reason, delivery, line);

    public T? Value { get; }
    public MarkingRefusal? Reason { get; }
    /// <summary>Set when <see cref="Reason"/> is <see cref="MarkingRefusal.SnapshotInvalid"/>: the existing reader's refusal.</summary>
    public RefusalCode? DeliveryReason { get; }
    /// <summary>For log verification: the 1-based line where the chain first fails.</summary>
    public int? Line { get; }
    public bool Accepted => Value is not null;

    internal static MarkingResult<T> Allow(T value) => new(value, null, null, null);
    internal static MarkingResult<T> Refuse(MarkingRefusal reason, RefusalCode? delivery = null, int? line = null)
        => new(null, reason, delivery, line);
    public override string ToString() => Accepted ? "accepted" :
        DeliveryReason is { } d ? $"{Reason} ({d})" : Line is { } l ? $"{Reason} (line {l})" : Reason.ToString()!;
}

internal sealed class MarkingRejected(MarkingRefusal reason, RefusalCode? delivery = null, int? line = null) : Exception
{
    public MarkingRefusal Reason { get; } = reason;
    public RefusalCode? DeliveryReason { get; } = delivery;
    public int? Line { get; } = line;
}

public static class CoreCategories
{
    /// <summary>The closed core set, in the order compiled policies list them. Same enum as annotation-snapshot-v1.</summary>
    public static readonly ImmutableArray<string> All =
        ["profanity", "sexual_dialogue", "sex_scene", "nudity", "violence", "gore", "disturbing", "substance_use", "crude"];

    public static bool IsCore(string? category) => category is not null && All.Contains(category);

    /// <summary>The phone's four quick buttons. Only two name exactly one core category; the reviewer picks the rest.</summary>
    public static readonly ImmutableArray<string> Quick = ["language", "violence", "sex_nudity", "other"];

    public static string? CoreFromHint(string? hint) => hint switch
    {
        "language" => "profanity",
        "violence" => "violence",
        _ when IsCore(hint) => hint,
        _ => null
    };
}

public sealed record CaptureTarget(string ServerRef, string ItemId, string MediaSourceId);

/// <summary>Kind is "source" or "filtered"; a filtered clock names the plan revision the viewer was watching.</summary>
public sealed record CaptureClock(string Kind, string? PlanRev = null)
{
    public static CaptureClock Source { get; } = new("source");
}

public sealed record CaptureWindow(long BeforeMs, long AfterMs);
public sealed record ObservedPosition(long SessionPositionMs, DateTimeOffset ReportedAt, bool Paused);

/// <summary>capture-v1: one press. Append-only; never edited and never filters anything.</summary>
public sealed record Capture(string Ref, DateTimeOffset CapturedAt, string Author, string Surface, CaptureTarget Target,
    CaptureClock Clock, string Kind, long AtMs, long? EndAtMs, CaptureWindow Window, ObservedPosition? Observed = null,
    string? Category = null, string? Note = null);

/// <summary>What the phone exports: a JSON array of capture-v1 objects.</summary>
public sealed record CaptureExport(ImmutableArray<Capture> Captures);

public sealed record MarkProvenance(ImmutableArray<string> From, string Method, string By, string License);

/// <summary>mark-v1: one revision. Category, severity, action and digest are present together or not at all.</summary>
public sealed record Mark(string Ref, int Rev, string BindingRev, long StartMs, long EndMs, string BoundsSource,
    string? Category, string? Subcategory, int? Severity, string? Action, string State, MarkProvenance Provenance,
    string EditedBy, string? ContentSha256)
{
    public const string CaptureWindowBounds = "capture-window";
    public const string ReviewerBounds = "reviewer";

    public bool IsComplete => Category is not null && Severity is not null && Action is not null && ContentSha256 is not null;

    /// <summary>The existing annotation-v1 formula (delivery README), so an accepted mark drops straight into a snapshot.</summary>
    public string? ComputeContentSha256() => Category is null || Severity is null || Action is null ? null :
        new AcceptedAnnotation(Ref, "", Category, Severity.Value, Action, StartMs, EndMs).ComputeContentSha256();

    internal AcceptedAnnotation ToAnnotation() =>
        new(Ref, ContentSha256!, Category!, Severity!.Value, Action!, StartMs, EndMs);
}

/// <summary>Exact values a person sets while refining. Category may be omitted when a subcategory names it.</summary>
public sealed record MarkEdit(long StartMs, long EndMs, string? Category, string? Subcategory, int Severity, string Action);

public sealed record TaxonomyConcept(string Id, string Label, string Broader, int? DefaultSeverity = null,
    string? DefaultAction = null, bool Deprecated = false);

/// <summary>taxonomy-v1: household and mapped concepts. Core is closed and never listed here.</summary>
public sealed record Taxonomy(string Rev, ImmutableArray<TaxonomyConcept> Concepts)
{
    public const int MaxNonCoreHops = 2;
    public static Taxonomy Empty { get; } = new("tax:0", []);

    /// <summary>The core category a concept rolls up to, or a refusal for unknown ids, cycles and chains too deep.</summary>
    public MarkingResult<string> RollUp(string id)
    {
        if (CoreCategories.IsCore(id))
            return MarkingResult<string>.Allow(id);
        var byId = new Dictionary<string, TaxonomyConcept>(StringComparer.Ordinal);
        foreach (var concept in Concepts)
            byId.TryAdd(concept.Id, concept);
        var seen = new HashSet<string>(StringComparer.Ordinal);
        var current = id;
        for (int hops = 0; ; hops++)
        {
            if (!seen.Add(current))
                return MarkingResult<string>.Refuse(MarkingRefusal.ConceptCycle);
            if (!byId.TryGetValue(current, out var concept))
                return MarkingResult<string>.Refuse(MarkingRefusal.UnknownConcept);
            if (CoreCategories.IsCore(concept.Broader))
                return MarkingResult<string>.Allow(concept.Broader);
            if (hops + 1 > MaxNonCoreHops)
                return MarkingResult<string>.Refuse(MarkingRefusal.ConceptTooDeep);
            current = concept.Broader;
        }
    }

    public bool IsDeprecated(string id) => Concepts.Any(c => c.Id == id && c.Deprecated);
}

public static class Roles
{
    public const string Admin = "admin", Reviewer = "reviewer", Marker = "marker", Viewer = "viewer", Suggester = "suggester";
    public static readonly ImmutableArray<string> All = [Admin, Reviewer, Marker, Viewer, Suggester];
}

public sealed record Member(string User, ImmutableArray<string> Roles, string? ProfileRef = null);
public sealed record ProfileFloor(string ProfileRef, ImmutableArray<CategoryRule> Floor, string ViewerMay);
public sealed record ApprovalSettings(bool SelfApproval, int SnapshotExpiryDays);

/// <summary>household-governance-v1. Admin implies reviewer; reviewer implies marker. A suggester may only add captures.</summary>
public sealed record Governance(string Rev, ImmutableArray<Member> Members, ImmutableArray<ProfileFloor> Profiles,
    ApprovalSettings Approval)
{
    public Member? Find(string user) => Members.FirstOrDefault(m => m.User == user);
    public bool IsMember(string user) => Find(user) is not null;
    public bool MayReview(string user) => Find(user)?.Roles is { } r && (r.Contains(Roles.Admin) || r.Contains(Roles.Reviewer));
    public bool MayMark(string user) => MayReview(user) || Find(user)?.Roles.Contains(Roles.Marker) == true;
    public bool IsSuggester(string user) => Find(user)?.Roles.Contains(Roles.Suggester) == true;
}

public sealed record AuditEvent(long Seq, DateTimeOffset At, string Actor, string Type, string Subject, string? Digest, string Prev);

public static class AuditTypes
{
    public const string CaptureAdd = "capture.add", MarkCreate = "mark.create", MarkEdit = "mark.edit",
        MarkAccept = "mark.accept", MarkReject = "mark.reject", MarkWithdraw = "mark.withdraw",
        SnapshotPublish = "snapshot.publish", TaxonomyChange = "taxonomy.change", GovernanceChange = "governance.change",
        PrefsChange = "prefs.change", ExceptionGrant = "exception.grant", ExceptionRevoke = "exception.revoke";

    public static readonly ImmutableArray<string> All = [CaptureAdd, MarkCreate, MarkEdit, MarkAccept, MarkReject,
        MarkWithdraw, SnapshotPublish, TaxonomyChange, GovernanceChange, PrefsChange, ExceptionGrant, ExceptionRevoke];
}

/// <summary>Source-clock position of a capture after any filtered-clock mapping.</summary>
public sealed record SourcePosition(long AtMs, long? EndAtMs);

public sealed record PublishedSnapshot(byte[] Utf8, AcceptedAnnotationSnapshot Snapshot, AuditEvent Event);
public sealed record CompiledPolicy(byte[] Utf8, HouseholdPolicy Policy);

/// <summary>One reviewer decision for the CLI: exact source-clock bounds and classification for one capture.</summary>
public sealed record ReviewerDecision(string CaptureRef, string MarkRef, MarkEdit Exact);
public sealed record ReviewerDecisions(ImmutableArray<ReviewerDecision> Marks);
