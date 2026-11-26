using System.Collections.Immutable;
using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using static Cleanyfin.Delivery.PlanValidator;

namespace Cleanyfin.Delivery;

public sealed record EvidenceSource(string Container, string VideoCodec, string AudioCodec, string TrackLayout, long DurationMs);

// Equality is ordinal and exact, including null: an unrecorded build never matches a known build.
public sealed record EvidenceTuple(string AdapterId, string AdapterRevision, string AppId, string AppVersion,
    string? AppBuild, string Backend, string DeviceMode, string? JellyfinVersion, string? PluginBuild, EvidenceSource Source);

public enum EvidenceStatus { Eligible, Experimental, Refused }
public enum Measurement { Pass, Fail, NotMeasured }
public sealed record EvidenceResult(Measurement Outcome, string Limit);
public sealed record EvidenceResults(EvidenceResult MarkedPicture, EvidenceResult MarkedSound,
    EvidenceResult KeptPictureAtJoin, EvidenceResult KeptSoundAtJoin)
{
    internal bool AllPass => MarkedPicture.Outcome == Measurement.Pass && MarkedSound.Outcome == Measurement.Pass &&
        KeptPictureAtJoin.Outcome == Measurement.Pass && KeptSoundAtJoin.Outcome == Measurement.Pass;
}

public sealed record SeamReport(string Silence, string Pop, string KeptPicture, string Sync, string KeptSoundLoss);

public sealed record EvidenceRow(EvidenceTuple Tuple, ImmutableArray<string> Actions, ImmutableArray<string> Controls,
    EvidenceResults Results, string EvidenceRef, DateOnly Date, EvidenceStatus Status, string Observation, SeamReport? TestSeams = null);

/// <summary>An immutable register from the closed v1 reader; only its exact rows can admit a request.</summary>
public sealed class EvidenceRegister
{
    private static readonly string[] ControlNames = ["straight-play", "seek-forward", "seek-back", "resume", "restart"];
    private static readonly Regex EvidencePath = new(@"\A(?:[A-Za-z0-9][A-Za-z0-9_.-]*/)*[A-Za-z0-9][A-Za-z0-9_.-]*\.[A-Za-z0-9]+\z", RegexOptions.NonBacktracking);
    private readonly ImmutableDictionary<EvidenceTuple, EvidenceRow> _rows;
    public ImmutableArray<EvidenceRow> Rows { get; }
    public const int MaxDocumentBytes = MaxPlanBytes;
    public const int MaxRows = 256;

    private EvidenceRegister(ImmutableArray<EvidenceRow> rows, ImmutableDictionary<EvidenceTuple, EvidenceRow> index) =>
        (Rows, _rows) = (rows, index);

    /// <summary>Experimental is never promoted by the lab flag: the returned row retains its status.</summary>
    public Decision<EvidenceRow> Admit(EvidenceTuple request, ImmutableArray<string> requires, bool allowExperimental = false)
    {
        if (!_rows.TryGetValue(request, out var row)) return Decision<EvidenceRow>.Refuse(RefusalCode.EvidenceTupleUnknown);
        if (row.TestSeams is not null || row.Status == EvidenceStatus.Refused) return Decision<EvidenceRow>.Refuse(RefusalCode.EvidenceRefused);
        if (requires.IsDefaultOrEmpty || requires.Any(feature => !row.Actions.Contains(feature, StringComparer.Ordinal)))
            return Decision<EvidenceRow>.Refuse(RefusalCode.EvidenceFeatureUncovered);
        if (row.Status == EvidenceStatus.Eligible && Complete(row)) return Decision<EvidenceRow>.Allow(row);
        if (row.Status == EvidenceStatus.Experimental && allowExperimental) return Decision<EvidenceRow>.Allow(row);
        return Decision<EvidenceRow>.Refuse(RefusalCode.EvidenceExperimentalOnly);
    }

    // Only this explicit request can admit a v2 test row. Even allowExperimental in Admit is v1-only.
    public Decision<EvidenceRow> AdmitTest(EvidenceTuple request, ImmutableArray<string> requires)
    {
        if (!_rows.TryGetValue(request, out var row)) return Decision<EvidenceRow>.Refuse(RefusalCode.EvidenceTupleUnknown);
        if (row.TestSeams is null) return Decision<EvidenceRow>.Refuse(RefusalCode.EvidenceRefused);
        if (requires.IsDefaultOrEmpty || requires.Any(f => !row.Actions.Contains(f, StringComparer.Ordinal)))
            return Decision<EvidenceRow>.Refuse(RefusalCode.EvidenceFeatureUncovered);
        return Decision<EvidenceRow>.Allow(row);
    }

    // The v2 supplemental register carries only positively measured test rows. Keep the v1 reader closed.
    public static Decision<EvidenceRegister> ParseTest(ReadOnlyMemory<byte> utf8) => ParseVersion(utf8, true);
    public static Decision<EvidenceRegister> Parse(ReadOnlyMemory<byte> utf8) => ParseVersion(utf8, false);

    private static Decision<EvidenceRegister> ParseVersion(ReadOnlyMemory<byte> utf8, bool test)
    {
        if (utf8.Length > MaxDocumentBytes) return Decision<EvidenceRegister>.Refuse(RefusalCode.LimitExceeded);
        try
        {
            using var doc = ReadDocument(utf8);
            var root = ReadObject(doc.RootElement, "registerVersion", "rows");
            Check(Integer(root["registerVersion"]) == (test ? 2 : 1), RefusalCode.UnsupportedVersion);
            var items = root["rows"];
            var rows = ImmutableArray.CreateBuilder<EvidenceRow>(ArrayCount(items, MaxRows));
            var index = ImmutableDictionary.CreateBuilder<EvidenceTuple, EvidenceRow>();
            foreach (var item in items.EnumerateArray())
            {
                var row = ReadRow(item, test);
                Check(index.TryAdd(row.Tuple, row), RefusalCode.DuplicateEvidenceTuple);
                rows.Add(row);
            }
            return Decision<EvidenceRegister>.Allow(new(rows.MoveToImmutable(), index.ToImmutable()));
        }
        catch (Rejected e) { return Decision<EvidenceRegister>.Refuse(e.Reason); }
        catch (Exception e) when (e is JsonException or DecoderFallbackException)
        { return Decision<EvidenceRegister>.Refuse(RefusalCode.InvalidJson); }
    }

    private static bool Complete(EvidenceRow row) => row.Results.AllPass && row.Controls.Length == ControlNames.Length &&
        row.Tuple.JellyfinVersion is not null && row.Tuple.PluginBuild is not null;

    private static EvidenceRow ReadRow(JsonElement item, bool test)
    {
        string[] fields = ["tuple", "actions", "controls", "results", "evidenceRef", "date", "status", "observation"];
        var f = ReadObject(item, test ? [.. fields, "tier", "gate", "seams"] : fields);
        var results = ReadObject(f["results"], "markedPicture", "markedSound", "keptPictureAtJoin", "keptSoundAtJoin");
        var path = Text(f["evidenceRef"]);
        Check(EvidencePath.IsMatch(path), RefusalCode.InvalidValue);
        Check(DateOnly.TryParseExact(Text(f["date"]), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None,
            out var date), RefusalCode.InvalidTimestamp);
        var status = Text(f["status"]) switch
        {
            "eligible" => EvidenceStatus.Eligible, "experimental" => EvidenceStatus.Experimental,
            "refused" => EvidenceStatus.Refused, _ => throw new Rejected(RefusalCode.InvalidValue)
        };
        var row = new EvidenceRow(ReadTuple(f["tuple"]), ReadChoices(f["actions"], 1, [Cut]), ReadChoices(f["controls"], 0, ControlNames),
            new(ReadResult(results["markedPicture"]), ReadResult(results["markedSound"]), ReadResult(results["keptPictureAtJoin"]),
                ReadResult(results["keptSoundAtJoin"])), path, date, status, Note(f["observation"]));
        Check(row.Status != EvidenceStatus.Eligible || Complete(row), RefusalCode.IncompleteEvidence);
        if (test)
        {
            var gate = ReadObject(f["gate"], "positions", "allTakesMeasured", "markedFrames", "markedSoundDetections", "positiveControlsPassed");
            Check(Text(f["tier"]) == "test" && status == EvidenceStatus.Experimental && row.Tuple.AppBuild != null &&
                row.Tuple.PluginBuild != null && row.Tuple.JellyfinVersion != null && row.Tuple.Source.AudioCodec is ("ac3" or "eac3") &&
                row.Controls.Length == ControlNames.Length && row.Results.MarkedPicture.Outcome == Measurement.Pass &&
                row.Results.MarkedSound.Outcome == Measurement.Pass && Integer(gate["positions"]) == 24 &&
                gate["allTakesMeasured"].ValueKind == JsonValueKind.True && Integer(gate["markedFrames"]) == 0 &&
                Integer(gate["markedSoundDetections"]) == 0 && gate["positiveControlsPassed"].ValueKind == JsonValueKind.True,
                RefusalCode.IncompleteEvidence);
            var seams = ReadObject(f["seams"], "silence", "pop", "keptPicture", "sync", "keptSoundLoss");
            row = row with { TestSeams = new(Note(seams["silence"]), Note(seams["pop"]), Note(seams["keptPicture"]),
                Note(seams["sync"]), Note(seams["keptSoundLoss"])) };
        }
        return row;
    }

    private static EvidenceTuple ReadTuple(JsonElement item)
    {
        var f = ReadObject(item, "adapterId", "adapterRevision", "appId", "appVersion", "appBuild", "backend", "deviceMode",
            "jellyfinVersion", "pluginBuild", "source");
        var src = ReadObject(f["source"], "container", "videoCodec", "audioCodec", "trackLayout", "durationMs");
        return new(Reference(f["adapterId"]), Reference(f["adapterRevision"]), Label(f["appId"]), Reference(f["appVersion"]),
            Optional(f["appBuild"], Digest), Reference(f["backend"]), Label(f["deviceMode"]), Optional(f["jellyfinVersion"], Reference),
            Optional(f["pluginBuild"], Digest), new(Reference(src["container"]), Reference(src["videoCodec"]), Reference(src["audioCodec"]),
                Label(src["trackLayout"]), Integer(src["durationMs"], 1)));
    }

    private static string? Optional(JsonElement value, Func<JsonElement, string> read) =>
        value.ValueKind == JsonValueKind.Null ? null : read(value);

    private static ImmutableArray<string> ReadChoices(JsonElement value, int min, string[] vocabulary)
    {
        var result = ImmutableArray.CreateBuilder<string>(ArrayCount(value, vocabulary.Length, min));
        foreach (var element in value.EnumerateArray())
        {
            var choice = Text(element);
            Check(vocabulary.Contains(choice, StringComparer.Ordinal), RefusalCode.InvalidValue);
            Check(!result.Contains(choice), RefusalCode.DuplicateReference);
            result.Add(choice);
        }
        return result.MoveToImmutable();
    }

    private static EvidenceResult ReadResult(JsonElement item)
    {
        var f = ReadObject(item, "outcome", "limit");
        var outcome = Text(f["outcome"]) switch
        {
            "pass" => Measurement.Pass, "fail" => Measurement.Fail,
            "not-measured" => Measurement.NotMeasured, _ => throw new Rejected(RefusalCode.InvalidValue)
        };
        return new(outcome, Note(f["limit"]));
    }

    private static string Label(JsonElement value)
    {
        var text = Text(value);
        Check(char.IsAsciiLetterOrDigit(text[0]) && !text.EndsWith(' ') && text.All(c =>
            char.IsAsciiLetterOrDigit(c) || c is ' ' or ':' or '.' or ',' or '/' or '-' or '_'), RefusalCode.InvalidValue);
        return text;
    }

    private static string Note(JsonElement value)
    {
        var text = Text(value, MaxDescriptionLength);
        Check(!string.IsNullOrWhiteSpace(text) && !text.Any(char.IsControl), RefusalCode.InvalidValue);
        return text;
    }
}
