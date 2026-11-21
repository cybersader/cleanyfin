using System.Collections.Immutable;
using System.Security.Cryptography;
using System.Text;

namespace Cleanyfin.Delivery;

public sealed record AcceptedAnnotation(string Ref, string ContentSha256, string Category,
    int Severity, string Action, long StartMs, long EndMs)
{
    /// <summary>The v1 content digest includes the ref and every semantic annotation field.</summary>
    public string ComputeContentSha256() => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(
        FormattableString.Invariant($"annotation-v1|{Ref}|{Category}|{Severity}|{Action}|{StartMs}|{EndMs}"))));
}

/// <summary>Supplied by the household after review of the exact source and its continuous presentation clock.</summary>
public sealed record AcceptedAnnotationSnapshot(string SnapshotRev, string Coverage,
    DateTimeOffset AcceptedAt, DateTimeOffset ExpiresAt, SourceBinding Binding,
    ImmutableArray<AcceptedAnnotation> Annotations);

public sealed record CategoryRule(string Category, int MinimumSeverity, bool Filter);

public sealed record HouseholdPolicy(string ProfileRef, string PolicyRev, string TrustRev,
    ImmutableArray<CategoryRule> Rules);

/// <summary>Independently verified identity of the exact immutable selected source, not a lookup fingerprint.</summary>
public sealed record VerifiedSourceIdentity(string ItemId, string MediaSourceId, string Sha256,
    long SizeBytes, long DurationMs);

/// <summary>
/// Caller supplies authenticated viewer/profile and a trusted clock watermark to detect rollback.
/// The policy and accepted snapshot must come from one consistent current household read.
/// </summary>
public sealed record ResolverRequest(string ViewerRef, string ProfileRef, string ServerRef,
    VerifiedSourceIdentity SelectedSource, TimeProvider Clock, DateTimeOffset ClockFloor, TimeSpan Validity);

public interface IPlanRevisionAllocator
{
    /// <summary>Reserve a fresh local revision, never reused by this authority, including after restart.</summary>
    string AllocateRevision();
}

/// <summary>Validated plan, its immutable v1 wire representation, and a separate resume timeline identity.</summary>
public sealed record ResolvedPlaybackPlan(PlaybackPlan Plan, ImmutableArray<byte> Utf8, string TimelineKey);
