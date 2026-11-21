using System.Collections.Immutable;
using System.Text;
using System.Text.Json;
using static Cleanyfin.Delivery.PlanValidator;

namespace Cleanyfin.Delivery;

/// <summary>Strict readers for local documents. Parsing cannot establish review, currency or authority.</summary>
public static class ResolverInputReader
{
    public const int MaxDocumentBytes = MaxPlanBytes;

    public static Decision<AcceptedAnnotationSnapshot> ParseSnapshot(ReadOnlyMemory<byte> utf8) =>
        Parse(utf8, RefusalCode.AbsentSnapshot, ReadSnapshot);

    public static Decision<HouseholdPolicy> ParsePolicy(ReadOnlyMemory<byte> utf8) =>
        Parse(utf8, RefusalCode.AuthorityMismatch, ReadPolicy);

    private static Decision<T> Parse<T>(ReadOnlyMemory<byte> utf8, RefusalCode absent,
        Func<JsonElement, T> read) where T : class
    {
        if (utf8.IsEmpty)
            return Decision<T>.Refuse(absent);
        if (utf8.Length > MaxDocumentBytes)
            return Decision<T>.Refuse(RefusalCode.LimitExceeded);
        try
        {
            using var doc = ReadDocument(utf8);
            return Decision<T>.Allow(read(doc.RootElement));
        }
        catch (Rejected e)
        {
            return Decision<T>.Refuse(e.Reason);
        }
        catch (Exception e) when (e is JsonException or DecoderFallbackException)
        {
            return Decision<T>.Refuse(RefusalCode.InvalidJson);
        }
        catch (OverflowException)
        {
            return Decision<T>.Refuse(RefusalCode.IntegerOverflow);
        }
    }

    private static AcceptedAnnotationSnapshot ReadSnapshot(JsonElement root)
    {
        var fields = ReadObject(root, "snapshotVersion", "snapshotRev", "coverage", "acceptedAt", "expiresAt",
            "binding", "annotations");
        Check(Integer(fields["snapshotVersion"]) == 1, RefusalCode.UnsupportedVersion);
        var binding = ReadSource(fields["binding"]);
        var items = fields["annotations"];
        var annotations = ImmutableArray.CreateBuilder<AcceptedAnnotation>(ArrayCount(items, MaxInputReferences));
        foreach (var item in items.EnumerateArray())
        {
            var mark = ReadObject(item, "ref", "contentSha256", "category", "severity", "action", "startMs", "endMs");
            annotations.Add(new AcceptedAnnotation(Reference(mark["ref"]), Digest(mark["contentSha256"]),
                Text(mark["category"], reason: RefusalCode.UnknownCategory), ReadSeverity(mark["severity"]),
                Text(mark["action"], reason: RefusalCode.UnsupportedAction), Integer(mark["startMs"]), Integer(mark["endMs"])));
        }
        var snapshot = new AcceptedAnnotationSnapshot(Reference(fields["snapshotRev"]), Text(fields["coverage"]),
            Timestamp(fields["acceptedAt"]), Timestamp(fields["expiresAt"]), binding, annotations.MoveToImmutable());
        ResolverInputValidation.CheckSnapshot(snapshot);
        return snapshot;
    }

    private static HouseholdPolicy ReadPolicy(JsonElement root)
    {
        var fields = ReadObject(root, "policyVersion", "profileRef", "policyRev", "trustRev", "rules");
        Check(Integer(fields["policyVersion"]) == 1, RefusalCode.UnsupportedVersion);
        var items = fields["rules"];
        var rules = ImmutableArray.CreateBuilder<CategoryRule>(ArrayCount(items, ResolverInputValidation.CategoryCount));
        foreach (var item in items.EnumerateArray())
        {
            var rule = ReadObject(item, "category", "minimumSeverity", "filter");
            var filter = rule["filter"];
            Check(filter.ValueKind is JsonValueKind.True or JsonValueKind.False, RefusalCode.InvalidValue);
            rules.Add(new CategoryRule(Text(rule["category"], reason: RefusalCode.UnknownCategory),
                ReadSeverity(rule["minimumSeverity"]), filter.GetBoolean()));
        }
        var policy = new HouseholdPolicy(Reference(fields["profileRef"]), Reference(fields["policyRev"]),
            Reference(fields["trustRev"]), rules.MoveToImmutable());
        _ = ResolverInputValidation.ReadRules(policy);
        return policy;
    }

    private static int ReadSeverity(JsonElement element)
    {
        long value = Integer(element, 0);
        Check(value <= 3, RefusalCode.InvalidValue);
        return (int)value;
    }
}

internal static class ResolverInputValidation
{
    internal const int CategoryCount = 9;

    private static bool IsCategory(string? category) => category is "profanity" or "sexual_dialogue" or
        "sex_scene" or "nudity" or "violence" or "gore" or "disturbing" or "substance_use" or "crude";

    internal static void CheckSnapshot(AcceptedAnnotationSnapshot snapshot)
    {
        Check(IsReference(snapshot.SnapshotRev) && snapshot.Coverage == ReviewedCoverage, RefusalCode.InvalidValue);
        Check(snapshot.AcceptedAt < snapshot.ExpiresAt, RefusalCode.InvalidValidityWindow);
        Check(!snapshot.Annotations.IsDefault, RefusalCode.InvalidValue);
        Check(snapshot.Annotations.Length <= MaxInputReferences, RefusalCode.LimitExceeded);
        var refs = new HashSet<string>(StringComparer.Ordinal);
        foreach (var mark in snapshot.Annotations)
        {
            Check(mark is not null && IsReference(mark.Ref), RefusalCode.InvalidValue);
            Check(refs.Add(mark!.Ref), RefusalCode.DuplicateReference);
            Check(IsCategory(mark.Category), RefusalCode.UnknownCategory);
            Check(mark.Severity is >= 0 and <= 3, RefusalCode.InvalidValue);
            Check(mark.Action is "skip" or Cut or "mute" or "mark", RefusalCode.UnsupportedAction);
            Check(mark.StartMs >= 0 && mark.StartMs < mark.EndMs && mark.EndMs <= snapshot.Binding.DurationMs,
                RefusalCode.InvalidInterval);
            Check(IsDigest(mark.ContentSha256), RefusalCode.InvalidDigest);
            Check(mark.ContentSha256 == mark.ComputeContentSha256(), RefusalCode.AnnotationDigestMismatch);
        }
    }

    internal static Dictionary<string, CategoryRule> ReadRules(HouseholdPolicy policy)
    {
        Check(IsReference(policy.ProfileRef) && IsReference(policy.PolicyRev) && IsReference(policy.TrustRev),
            RefusalCode.InvalidValue);
        Check(!policy.Rules.IsDefault, RefusalCode.InvalidValue);
        Check(policy.Rules.Length <= CategoryCount, RefusalCode.LimitExceeded);
        var rules = new Dictionary<string, CategoryRule>(StringComparer.Ordinal);
        foreach (var rule in policy.Rules)
        {
            Check(rule is not null, RefusalCode.InvalidValue);
            Check(IsCategory(rule!.Category), RefusalCode.UnknownCategory);
            Check(rule.MinimumSeverity is >= 0 and <= 3, RefusalCode.InvalidValue);
            Check(rules.TryAdd(rule.Category, rule), RefusalCode.ConflictingInput);
        }
        return rules;
    }
}
