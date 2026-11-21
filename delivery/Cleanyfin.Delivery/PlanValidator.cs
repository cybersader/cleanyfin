using System.Collections.Immutable;
using System.Globalization;
using System.Text;
using System.Text.Json;

namespace Cleanyfin.Delivery;

public static class PlanValidator
{
    public const int MaxPlanBytes = 1024 * 1024;
    public const int MaxOperations = 4096;
    public const int MaxInputReferences = 8192;
    public const int MaxTracks = 256;
    public const int MaxReferenceLength = 256;
    public const int MaxDescriptionLength = 512;
    public const long MaxJsonInteger = 9_007_199_254_740_991;
    public const string SourceOrigin = "source-presentation-zero";
    public const string ReviewedCoverage = "reviewed-marks-only";
    public const string Cut = "cut";

    private static readonly UTF8Encoding StrictUtf8 = new(false, true);
    private static readonly string[] TimestampFormats =
        ["yyyy-MM-dd'T'HH:mm:ss'Z'", "yyyy-MM-dd'T'HH:mm:ss.FFFFFFF'Z'"];

    public static Decision<PlaybackPlan> Parse(ReadOnlyMemory<byte> utf8, TimeProvider clock,
        VerifiedPlanContext? verified)
    {
        ArgumentNullException.ThrowIfNull(clock);
        if (utf8.IsEmpty)
            return Decision<PlaybackPlan>.Refuse(RefusalCode.AbsentPlan);
        if (utf8.Length > MaxPlanBytes)
            return Decision<PlaybackPlan>.Refuse(RefusalCode.PlanTooLarge);

        try
        {
            using var doc = ReadDocument(utf8);
            var root = ReadObject(doc.RootElement, "planVersion", "requires", "revision", "issuedAt", "expiresAt",
                "source", "authority", "inputs", "operations", "?extensions");
            Check(Integer(root["planVersion"]) == 1, RefusalCode.UnsupportedVersion);
            var requires = ReadRequires(root["requires"]);
            var revision = Reference(root["revision"]);
            var issuedAt = Timestamp(root["issuedAt"]);
            var expiresAt = Timestamp(root["expiresAt"]);
            Check(issuedAt < expiresAt, RefusalCode.InvalidValidityWindow);
            var now = clock.GetUtcNow();
            Check(verified?.ClockFloor is not { } floor || now >= floor, RefusalCode.ClockRollback);
            Check(now >= issuedAt, RefusalCode.NotYetValid);
            Check(now < expiresAt, RefusalCode.Expired);

            var source = ReadSource(root["source"]);
            var authority = ReadAuthority(root["authority"]);
            var inputs = ReadInputs(root["inputs"], out var refs);
            var operations = ReadOperations(root["operations"], source.DurationMs, requires, refs);
            if (root.TryGetValue("extensions", out var extensions))
                ReadExtensions(extensions);

            VerifyCurrent(verified, revision, source, authority, inputs);
            return Decision<PlaybackPlan>.Allow(new PlaybackPlan(requires, revision, issuedAt, expiresAt,
                source, authority, inputs, operations));
        }
        catch (Rejected e)
        {
            return Decision<PlaybackPlan>.Refuse(e.Reason);
        }
        catch (Exception e) when (e is JsonException or DecoderFallbackException)
        {
            return Decision<PlaybackPlan>.Refuse(RefusalCode.InvalidJson);
        }
        catch (OverflowException)
        {
            return Decision<PlaybackPlan>.Refuse(RefusalCode.IntegerOverflow);
        }
    }

    private static ImmutableArray<string> ReadRequires(JsonElement value)
    {
        var count = ArrayCount(value, MaxInputReferences);
        var features = ImmutableArray.CreateBuilder<string>(count);
        foreach (var entry in value.EnumerateArray())
        {
            Check(Text(entry) == Cut, RefusalCode.UnsupportedFeature);
            Check(features.Count == 0, RefusalCode.DuplicateReference);
            features.Add(Cut);
        }
        return features.MoveToImmutable();
    }

    internal static SourceBinding ReadSource(JsonElement value)
    {
        var fields = ReadObject(value, "serverRef", "itemId", "mediaSourceId", "sha256", "sizeBytes",
            "timelineRef", "bindingRev", "durationMs", "origin", "tracks");
        var duration = Integer(fields["durationMs"], 1);
        var tracks = fields["tracks"];
        var clocks = ImmutableArray.CreateBuilder<TrackClock>(ArrayCount(tracks, MaxTracks, min: 1));
        foreach (var entry in tracks.EnumerateArray())
        {
            var track = ReadObject(entry, "index", "kind", "timeBase", "anchorPts", "anchorSourceMs");
            var timeBase = track["timeBase"];
            Check(timeBase.ValueKind == JsonValueKind.Array && timeBase.GetArrayLength() == 2,
                RefusalCode.InvalidValue);
            clocks.Add(new TrackClock(Integer(track["index"], 0), Text(track["kind"]),
                Integer(timeBase[0], 1), Integer(timeBase[1], 1), Integer(track["anchorPts"]),
                Integer(track["anchorSourceMs"], 0)));
        }
        var source = new SourceBinding(Reference(fields["serverRef"]), Reference(fields["itemId"]),
            Reference(fields["mediaSourceId"]), Digest(fields["sha256"]), Integer(fields["sizeBytes"], 1),
            Reference(fields["timelineRef"]), Reference(fields["bindingRev"]), duration, Text(fields["origin"]),
            clocks.MoveToImmutable());
        CheckSource(source);
        return source;
    }

    internal static void CheckSource(SourceBinding source)
    {
        Check(IsReference(source.ServerRef) && IsReference(source.ItemId) && IsReference(source.MediaSourceId) &&
            IsReference(source.TimelineRef) && IsReference(source.BindingRev), RefusalCode.InvalidValue);
        Check(IsDigest(source.Sha256), RefusalCode.InvalidDigest);
        Check(source.SizeBytes <= MaxJsonInteger && source.DurationMs <= MaxJsonInteger, RefusalCode.IntegerOverflow);
        Check(source.SizeBytes > 0 && source.DurationMs > 0 && source.Origin == SourceOrigin, RefusalCode.InvalidValue);
        _ = CutTimeline.MillisecondsToTicks(source.DurationMs);
        Check(!source.Tracks.IsDefaultOrEmpty, RefusalCode.InvalidValue);
        Check(source.Tracks.Length <= MaxTracks, RefusalCode.LimitExceeded);
        var indices = new HashSet<long>();
        foreach (var track in source.Tracks)
        {
            Check(track is not null, RefusalCode.InvalidValue);
            Check(track!.Index <= MaxJsonInteger && track.TimeBaseNumerator <= MaxJsonInteger &&
                track.TimeBaseDenominator <= MaxJsonInteger && track.AnchorPts is >= -MaxJsonInteger and <= MaxJsonInteger &&
                track.AnchorSourceMs <= MaxJsonInteger, RefusalCode.IntegerOverflow);
            Check(track.Index >= 0 && track.Kind is "video" or "audio" or "subtitle" &&
                track.TimeBaseNumerator > 0 && track.TimeBaseDenominator > 0 &&
                track.AnchorSourceMs >= 0 && track.AnchorSourceMs < source.DurationMs, RefusalCode.InvalidValue);
            Check(indices.Add(track.Index), RefusalCode.DuplicateTrackIndex);
        }
    }

    private static PlanAuthority ReadAuthority(JsonElement value)
    {
        var fields = ReadObject(value, "viewerRef", "profileRef", "policyRev", "trustRev");
        return new PlanAuthority(Reference(fields["viewerRef"]), Reference(fields["profileRef"]),
            Reference(fields["policyRev"]), Reference(fields["trustRev"]));
    }

    private static PlanInputs ReadInputs(JsonElement value, out HashSet<string> refs)
    {
        var fields = ReadObject(value, "snapshotRev", "coverage", "annotations");
        Check(Text(fields["coverage"]) == ReviewedCoverage, RefusalCode.InvalidValue);
        var annotations = fields["annotations"];
        var count = ArrayCount(annotations, MaxInputReferences);
        refs = new HashSet<string>(StringComparer.Ordinal);
        var inputs = ImmutableArray.CreateBuilder<AnnotationInput>(count);
        foreach (var entry in annotations.EnumerateArray())
        {
            var annotation = ReadObject(entry, "ref", "contentSha256");
            var reference = Reference(annotation["ref"]);
            Check(refs.Add(reference), RefusalCode.DuplicateReference);
            inputs.Add(new AnnotationInput(reference, Digest(annotation["contentSha256"])));
        }
        return new PlanInputs(Reference(fields["snapshotRev"]), ReviewedCoverage, inputs.MoveToImmutable());
    }

    private static ImmutableArray<CutOperation> ReadOperations(JsonElement value, long duration,
        ImmutableArray<string> requires, HashSet<string> knownRefs)
    {
        var operations = ImmutableArray.CreateBuilder<CutOperation>(ArrayCount(value, MaxOperations));
        var usedRefs = new HashSet<string>(StringComparer.Ordinal);
        int remainingRefs = MaxInputReferences;
        long previousEnd = 0;
        foreach (var entry in value.EnumerateArray())
        {
            var operation = ReadObject(entry, "action", "startMs", "endMs", "from");
            Check(Text(operation["action"]) == Cut, RefusalCode.UnsupportedAction);
            Check(requires.Contains(Cut), RefusalCode.MissingRequiredFeature);
            var start = Integer(operation["startMs"]);
            var end = Integer(operation["endMs"]);
            Check(start >= 0 && start < end && end <= duration, RefusalCode.InvalidInterval);
            Check(start >= previousEnd, RefusalCode.UnsortedOrOverlappingOperations);
            previousEnd = end;

            var from = operation["from"];
            var count = ArrayCount(from, remainingRefs, min: 1);
            remainingRefs -= count;
            var refs = ImmutableArray.CreateBuilder<string>(count);
            foreach (var reference in from.EnumerateArray())
            {
                var name = Reference(reference);
                Check(knownRefs.Contains(name), RefusalCode.UnknownReference);
                Check(usedRefs.Add(name), RefusalCode.DuplicateReference);
                refs.Add(name);
            }
            operations.Add(new CutOperation(start, end, refs.MoveToImmutable()));
        }
        return operations.MoveToImmutable();
    }

    private static void ReadExtensions(JsonElement value)
    {
        // An optional, inert description; do not retain it or open an escape hatch for semantic fields.
        var fields = ReadObject(value, "?description");
        if (fields.TryGetValue("description", out var description))
        {
            var text = Text(description, MaxDescriptionLength);
            Check(text.Any(char.IsAsciiLetterOrDigit) && text.All(c => char.IsAsciiLetterOrDigit(c) ||
                " .,!?'-_()".Contains(c)), RefusalCode.InvalidValue);
        }
    }

    private static void VerifyCurrent(VerifiedPlanContext? verified, string revision, SourceBinding source,
        PlanAuthority authority, PlanInputs inputs)
    {
        if (verified is null || verified.Source.Tracks.IsDefault || verified.AnnotationDigests is null)
            throw new Rejected(RefusalCode.UnverifiedBinding);
        Check(revision == verified.ActiveRevision, RefusalCode.RevisionMismatch);
        // Record equality alone would compare the immutable array's backing storage by identity.
        Check(source with { Tracks = verified.Source.Tracks } == verified.Source &&
            source.Tracks.SequenceEqual(verified.Source.Tracks), RefusalCode.BindingMismatch);
        Check(authority == verified.Authority, RefusalCode.AuthorityMismatch);
        Check(inputs.SnapshotRev == verified.SnapshotRev && inputs.Coverage == verified.Coverage,
            RefusalCode.SnapshotMismatch);
        foreach (var annotation in inputs.Annotations)
        {
            Check(verified.AnnotationDigests.TryGetKey(annotation.Ref, out var key) && key == annotation.Ref,
                RefusalCode.UnknownReference);
            Check(verified.AnnotationDigests[annotation.Ref] == annotation.ContentSha256,
                RefusalCode.AnnotationDigestMismatch);
        }
    }

    internal static JsonDocument ReadDocument(ReadOnlyMemory<byte> utf8)
    {
        // Callers bound input length first. Reject broken UTF-8 even in ignored metadata.
        _ = StrictUtf8.GetCharCount(utf8.Span);
        return JsonDocument.Parse(utf8, new JsonDocumentOptions { MaxDepth = 8 });
    }

    internal static Dictionary<string, JsonElement> ReadObject(JsonElement element, params string[] names)
    {
        Check(element.ValueKind == JsonValueKind.Object, RefusalCode.InvalidValue);
        var fields = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
        foreach (var property in element.EnumerateObject())
        {
            string name;
            try { name = property.Name; }
            catch (InvalidOperationException) { throw new Rejected(RefusalCode.InvalidJson); }
            Check(names.Any(n => n[0] == '?' ? n.AsSpan(1).SequenceEqual(name.AsSpan()) : n == name),
                RefusalCode.UnknownField);
            Check(fields.TryAdd(name, property.Value), RefusalCode.DuplicateKey);
        }
        foreach (var name in names)
            Check(name[0] == '?' || fields.ContainsKey(name), RefusalCode.MissingField);
        return fields;
    }

    internal static int ArrayCount(JsonElement value, int max, int min = 0)
    {
        Check(value.ValueKind == JsonValueKind.Array, RefusalCode.InvalidValue);
        var count = value.GetArrayLength();
        Check(count <= max, RefusalCode.LimitExceeded);
        Check(count >= min, RefusalCode.InvalidValue);
        return count;
    }

    internal static long Integer(JsonElement value, long min = -MaxJsonInteger)
    {
        Check(value.ValueKind == JsonValueKind.Number, RefusalCode.InvalidValue);
        if (!value.TryGetInt64(out var number))
            throw new Rejected(value.GetRawText().AsSpan().IndexOfAny('.', 'e', 'E') >= 0
                ? RefusalCode.InvalidValue : RefusalCode.IntegerOverflow);
        Check(number >= -MaxJsonInteger && number <= MaxJsonInteger, RefusalCode.IntegerOverflow);
        Check(number >= min, RefusalCode.InvalidValue);
        return number;
    }

    internal static string Text(JsonElement value, int max = MaxReferenceLength,
        RefusalCode reason = RefusalCode.InvalidValue)
    {
        Check(value.ValueKind == JsonValueKind.String, reason);
        string text;
        try { text = value.GetString()!; }
        catch (InvalidOperationException) { throw new Rejected(RefusalCode.InvalidJson); }
        Check(text.Length > 0, reason);
        Check(text.Length <= max, RefusalCode.LimitExceeded);
        return text;
    }

    internal static string Reference(JsonElement value)
    {
        var text = Text(value);
        Check(IsReference(text), RefusalCode.InvalidValue);
        return text;
    }

    internal static bool IsReference(string? text) => text is { Length: > 0 and <= MaxReferenceLength } &&
        char.IsAsciiLetterOrDigit(text[0]) && text.All(c => char.IsAsciiLetterOrDigit(c) || c is ':' or '-' or '_' or '.');

    internal static string Digest(JsonElement value)
    {
        var text = Text(value, reason: RefusalCode.InvalidDigest);
        Check(IsDigest(text), RefusalCode.InvalidDigest);
        return text;
    }

    internal static bool IsDigest(string? text) => text is { Length: 64 } &&
        text.All(c => char.IsAsciiDigit(c) || c is >= 'a' and <= 'f');

    internal static DateTimeOffset Timestamp(JsonElement value)
    {
        var text = Text(value, reason: RefusalCode.InvalidTimestamp);
        if ((text.Length == 20 || text.Length is >= 22 and <= 28 && text[19] == '.') &&
            DateTimeOffset.TryParseExact(text, TimestampFormats, CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var parsed))
            return parsed;
        throw new Rejected(RefusalCode.InvalidTimestamp);
    }

    internal static void Check(bool ok, RefusalCode reason)
    {
        if (!ok)
            throw new Rejected(reason);
    }

    internal sealed class Rejected(RefusalCode reason) : Exception
    {
        public RefusalCode Reason { get; } = reason;
    }
}
