using System.Collections.Immutable;
using System.Numerics;

namespace Cleanyfin.Delivery;

// Values and names are stable. Refusals never echo plan contents or credentials.
public enum RefusalCode
{
    AbsentPlan = 1,
    PlanTooLarge = 2,
    InvalidJson = 3,
    DuplicateKey = 4,
    UnknownField = 5,
    MissingField = 6,
    InvalidValue = 7,
    UnsupportedVersion = 8,
    UnsupportedFeature = 9,
    UnsupportedAction = 10,
    MissingRequiredFeature = 11,
    LimitExceeded = 12,
    DuplicateReference = 13,
    UnknownReference = 14,
    InvalidDigest = 15,
    InvalidTimestamp = 16,
    InvalidValidityWindow = 17,
    NotYetValid = 18,
    Expired = 19,
    ClockRollback = 20,
    DuplicateTrackIndex = 21,
    InvalidInterval = 22,
    UnsortedOrOverlappingOperations = 23,
    IntegerOverflow = 24,
    UnverifiedBinding = 25,
    BindingMismatch = 26,
    AuthorityMismatch = 27,
    RevisionMismatch = 28,
    SnapshotMismatch = 29,
    AnnotationDigestMismatch = 30,
    UnverifiedPlayerClock = 31,
    NoKeptRanges = 32,
    NoCuts = 33,
    InvalidOrigin = 34,
    InvalidIdentifier = 35
}

public sealed class Decision<T> where T : class
{
    private Decision(T? value, RefusalCode? reason) => (Value, Reason) = (value, reason);
    public T? Value { get; }
    public RefusalCode? Reason { get; }
    public bool Accepted => Value is not null;
    internal static Decision<T> Allow(T value) => new(value, null);
    internal static Decision<T> Refuse(RefusalCode reason) => new(null, reason);
}

public sealed record TrackClock(long Index, string Kind, long TimeBaseNumerator,
    long TimeBaseDenominator, long AnchorPts, long AnchorSourceMs)
{
    // Exact source seconds as a rational pair. A track tick need not land on a plan millisecond.
    public (BigInteger Numerator, BigInteger Denominator) ToSourceSeconds(long pts) => (
        (BigInteger)AnchorSourceMs * TimeBaseDenominator
            + ((BigInteger)pts - AnchorPts) * TimeBaseNumerator * 1000,
        (BigInteger)TimeBaseDenominator * 1000);
}

public sealed record SourceBinding(string ServerRef, string ItemId, string MediaSourceId, string Sha256,
    long SizeBytes, string TimelineRef, string BindingRev, long DurationMs, string Origin,
    ImmutableArray<TrackClock> Tracks);

public sealed record PlanAuthority(string ViewerRef, string ProfileRef, string PolicyRev, string TrustRev);
public sealed record AnnotationInput(string Ref, string ContentSha256);
public sealed record PlanInputs(string SnapshotRev, string Coverage, ImmutableArray<AnnotationInput> Annotations);
public sealed record CutOperation(long StartMs, long EndMs, ImmutableArray<string> From);

/// <summary>
/// Trusted facts supplied by the caller, not derived from the plan JSON. The caller verifies the exact
/// source bytes, continuous presentation clock, current revision, authority and accepted snapshot first.
/// ClockFloor is an optional trusted time watermark for detecting rollback; this library stores no state.
/// </summary>
public sealed record VerifiedPlanContext(string ActiveRevision, SourceBinding Source, PlanAuthority Authority,
    string SnapshotRev, string Coverage, ImmutableDictionary<string, string> AnnotationDigests,
    DateTimeOffset? ClockFloor = null);

/// <summary>An immutable snapshot validated against a clock and verified facts at parse time.</summary>
public sealed class PlaybackPlan
{
    internal PlaybackPlan(ImmutableArray<string> requires, string revision, DateTimeOffset issuedAt,
        DateTimeOffset expiresAt, SourceBinding source, PlanAuthority authority, PlanInputs inputs,
        ImmutableArray<CutOperation> operations)
    {
        (Requires, Revision, IssuedAt, ExpiresAt) = (requires, revision, issuedAt, expiresAt);
        (Source, Authority, Inputs, Operations) = (source, authority, inputs, operations);
        Timeline = new CutTimeline(source.DurationMs, operations);
    }

    public int PlanVersion => 1;
    public ImmutableArray<string> Requires { get; }
    public string Revision { get; }
    public DateTimeOffset IssuedAt { get; }
    public DateTimeOffset ExpiresAt { get; }
    public SourceBinding Source { get; }
    public PlanAuthority Authority { get; }
    public PlanInputs Inputs { get; }
    public ImmutableArray<CutOperation> Operations { get; }
    public CutTimeline Timeline { get; }
}
