using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json.Nodes;
using Cleanyfin.Delivery;
using Json.Schema;

namespace Delivery.Contract.Tests;

[Trait("Category", "Eligibility")]
public class EligibilityTests
{
    private static readonly byte[] Seed = File.ReadAllBytes(Path.Combine(AppContext.BaseDirectory, "evidence-register-v1.json"));
    private static readonly JsonSchema Schema = JsonSchema.FromText(File.ReadAllText(
        Path.Combine(AppContext.BaseDirectory, "evidence-register-v1.schema.json")));
    private static readonly string[] Metrics = ["markedPicture", "markedSound", "keptPictureAtJoin", "keptSoundAtJoin"];

    [Fact]
    public void SeedIsValidAndProductionAllowlistEmpty()
    {
        Assert.True(SchemaAccepts(Seed));
        var register = Read(Seed);
        Assert.Collection(register.Rows.Take(5), row => Assert.Equal(EvidenceStatus.Experimental, row.Status),
            row => Assert.Equal(EvidenceStatus.Experimental, row.Status), row => Assert.Equal(EvidenceStatus.Refused, row.Status),
            row => Assert.Equal(EvidenceStatus.Refused, row.Status), row => Assert.Equal(EvidenceStatus.Refused, row.Status));
        // Common-format rows (format-matrix.py): desktop lab mpv only, never a TV tuple, never eligible. An exact layout is
        // experimental (lab flag only); an inexact one is refused.
        Assert.All(register.Rows.Skip(5), row =>
        {
            Assert.Equal(("mpv", "mpv"), (row.Tuple.AppId, row.Tuple.Backend));
            Assert.Contains(row.Tuple.AdapterRevision, new[] { MpvEdlRenderer.AdapterRevision, MpvEdlRenderer.SubtitleAdapterRevision,
                MpvEdlRenderer.SplitAdapterRevision, MpvEdlRenderer.SplitSubtitleAdapterRevision });
            if (MpvEdlRenderer.IsSubtitleRevision(row.Tuple.AdapterRevision))
                Assert.Contains(" subtitle ", row.Tuple.Source.TrackLayout);
            Assert.StartsWith("Linux lab container, desktop mpv, decoded PCM", row.Tuple.DeviceMode);
            Assert.NotEqual(EvidenceStatus.Eligible, row.Status);
            Assert.Equal(row.Status == EvidenceStatus.Experimental, row.Results.MarkedPicture.Outcome == Measurement.Pass &&
                row.Results.MarkedSound.Outcome == Measurement.Pass && row.Results.KeptPictureAtJoin.Outcome == Measurement.Pass &&
                row.Results.KeptSoundAtJoin.Outcome == Measurement.Pass);
            Assert.True(File.Exists(Path.Combine(RepoRoot(), row.EvidenceRef)), row.EvidenceRef);
        });
        Assert.All(register.Rows, row => Assert.False(register.Admit(row.Tuple, ["cut"]).Accepted));
    }

    [Theory]
    [InlineData("/registerVersion", "2", RefusalCode.UnsupportedVersion)]
    [InlineData("/extra", "null", RefusalCode.UnknownField)]
    [InlineData("/rows/0/tuple/source/extra", "true", RefusalCode.UnknownField)]
    [InlineData("/rows/0/tuple/appVersion", "\"*\"", RefusalCode.InvalidValue)]
    [InlineData("/rows/0/tuple/deviceMode", "\"Any*\"", RefusalCode.InvalidValue)]
    [InlineData("/rows/0/tuple/backend", "\"mpv\\n\"", RefusalCode.InvalidValue)]
    [InlineData("/rows/0/tuple/appBuild", "\"ABC\"", RefusalCode.InvalidDigest)]
    [InlineData("/rows/0/tuple/source/durationMs", "0", RefusalCode.InvalidValue)]
    [InlineData("/rows/0/actions", "[\"mute\"]", RefusalCode.InvalidValue)]
    [InlineData("/rows/0/controls", "[\"resume\",\"resume\"]", RefusalCode.DuplicateReference)]
    [InlineData("/rows/0/results/markedSound/outcome", "\"maybe\"", RefusalCode.InvalidValue)]
    [InlineData("/rows/0/results/markedSound/limit", "\" \"", RefusalCode.InvalidValue)]
    [InlineData("/rows/0/evidenceRef", "\"../../receipt.md\"", RefusalCode.InvalidValue)]
    [InlineData("/rows/0/date", "\"2026-11\"", RefusalCode.InvalidTimestamp)]
    [InlineData("/rows/0/status", "\"eligible\"", RefusalCode.IncompleteEvidence)]
    public void SchemaAndReaderRejectBadDocuments(string pointer, string value, RefusalCode reason)
    {
        var parts = pointer.Split('/').Skip(1).ToArray();
        var doc = Document();
        JsonNode parent = doc;
        foreach (var part in parts[..^1]) parent = parent is JsonArray array ? array[int.Parse(part)]! : parent[part]!;
        parent[parts[^1]] = JsonNode.Parse(value);
        var bytes = Bytes(doc);
        Assert.False(SchemaAccepts(bytes));
        Assert.Equal(reason, EvidenceRegister.Parse(bytes).Reason);
    }

    [Fact]
    public void RejectsDuplicateKeysAndExactTuplesEvenIfNotesChange()
    {
        var duplicateKey = Encoding.UTF8.GetString(Seed).Replace("\"registerVersion\": 1", "\"registerVersion\": 1, \"register\\u0056ersion\": 1");
        Assert.Equal(RefusalCode.DuplicateKey, EvidenceRegister.Parse(Encoding.UTF8.GetBytes(duplicateKey)).Reason);
        var doc = Document();
        var rows = doc["rows"]!.AsArray();
        rows.Add(rows[0]!.DeepClone());
        Assert.False(SchemaAccepts(Bytes(doc)));
        Assert.Equal(RefusalCode.DuplicateEvidenceTuple, EvidenceRegister.Parse(Bytes(doc)).Reason);
        rows[^1]!["observation"] = "Differently worded note, still the same tuple.";
        Assert.Equal(RefusalCode.DuplicateEvidenceTuple, EvidenceRegister.Parse(Bytes(doc)).Reason);
    }

    [Fact]
    public void OnlyExactTupleCanAdmitCut()
    {
        var register = Read(Bytes(EligibleTestDocument()));
        var original = register.Rows[0].Tuple;
        Assert.Equal(EvidenceStatus.Eligible, register.Admit(original, ["cut"]).Value!.Status);
        foreach (var altered in new[] {
            original with { AppId = "Another app" }, original with { AppVersion = "1.1.3" },
            original with { AppBuild = null }, original with { Backend = "another-backend" },
            original with { DeviceMode = "Physical TV" }, original with { AdapterRevision = "2" },
            original with { AdapterId = "another-adapter" }, original with { JellyfinVersion = "10.11.12" },
            original with { PluginBuild = new string('b', 64) }, original with { Source = original.Source with { AudioCodec = "opus" } },
            original with { Source = original.Source with { TrackLayout = "different tracks" } } })
            Assert.Equal(RefusalCode.EvidenceTupleUnknown, register.Admit(altered, ["cut"], allowExperimental: true).Reason);
        Assert.Equal(RefusalCode.EvidenceFeatureUncovered, register.Admit(original, ["cut", "mute"], allowExperimental: true).Reason);
    }

    [Theory]
    [InlineData("fail")]
    [InlineData("not-measured")]
    public void EveryMeasurementIsRequiredForEligibility(string outcome)
    {
        foreach (var metric in Metrics)
        {
            var doc = EligibleTestDocument();
            var row = doc["rows"]![0]!;
            row["results"]![metric]!["outcome"] = outcome;
            Assert.False(SchemaAccepts(Bytes(doc)));
            Assert.Equal(RefusalCode.IncompleteEvidence, EvidenceRegister.Parse(Bytes(doc)).Reason);
            row["status"] = "experimental";
            Assert.True(SchemaAccepts(Bytes(doc)));
            var parsed = Read(Bytes(doc));
            var tuple = parsed.Rows[0].Tuple;
            Assert.Equal(RefusalCode.EvidenceExperimentalOnly, parsed.Admit(tuple, ["cut"]).Reason);
            Assert.Equal(EvidenceStatus.Experimental, parsed.Admit(tuple, ["cut"], allowExperimental: true).Value!.Status);
        }
    }

    [Fact]
    public void ExperimentalFlagDoesNotAdmitRefusedRowsOrUncoveredFeatures()
    {
        var register = Read(Seed);
        var measured = register.Rows[0].Tuple;
        Assert.Equal(EvidenceStatus.Experimental, register.Admit(measured, ["cut"], allowExperimental: true).Value!.Status);
        Assert.Equal(RefusalCode.EvidenceFeatureUncovered, register.Admit(measured, ["mute"], allowExperimental: true).Reason);
        Assert.Equal(RefusalCode.EvidenceRefused, register.Admit(register.Rows[4].Tuple, ["cut"], allowExperimental: true).Reason);
    }

    [Fact]
    public void AddingEligibleRowNeverAdmitsADifferentTuple()
    {
        var doc = EligibleTestDocument();
        Assert.True(SchemaAccepts(Bytes(doc)));
        var baseline = Read(Seed);
        var added = doc["rows"]![0]!.DeepClone();
        doc = Document();
        doc["rows"]!.AsArray().Add(added);
        for (var i = 0; i < 100; i++)
        {
            added["tuple"]!["appVersion"] = $"synthetic-{i}";
            var expanded = Read(Bytes(doc));
            var newTuple = expanded.Rows[^1].Tuple;
            Assert.Equal(EvidenceStatus.Eligible, expanded.Admit(newTuple, ["cut"]).Value!.Status);
            foreach (var other in baseline.Rows.Select(r => r.Tuple).Append(newTuple with { AppVersion = $"synthetic-{i + 1}" }))
                Assert.Equal(baseline.Admit(other, ["cut"]).Reason, expanded.Admit(other, ["cut"]).Reason);
        }
    }

    [Fact]
    public void EligibleEvidenceReferencesExistInTheCheckout()
    {
        Assert.Empty(MissingEvidence(Read(Seed)));
        var fake = EligibleTestDocument();
        fake["rows"]![0]!["evidenceRef"] = "delivery/evidence/no-such-presentation-receipt.md";
        Assert.Equal("delivery/evidence/no-such-presentation-receipt.md", Assert.Single(MissingEvidence(Read(Bytes(fake)))));
    }

    private static IEnumerable<string> MissingEvidence(EvidenceRegister register, [CallerFilePath] string testSource = "")
    {
        // A review tripwire, not proof that the cited file supports the row.
        var checkout = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(testSource)!, "..", ".."));
        Assert.True(File.Exists(Path.Combine(checkout, "delivery/evidence/register-v1.json")), "Test needs the source checkout.");
        return register.Rows.Where(row => row.Status == EvidenceStatus.Eligible && !File.Exists(Path.Combine(checkout, row.EvidenceRef)))
            .Select(row => row.EvidenceRef);
    }

    private static JsonObject EligibleTestDocument()
    {
        var doc = Document();
        var row = doc["rows"]![0]!.DeepClone();
        doc["rows"] = new JsonArray(row);
        row["controls"] = new JsonArray("straight-play", "seek-forward", "seek-back", "resume", "restart");
        foreach (var metric in Metrics) row["results"]![metric]!["outcome"] = "pass";
        row["status"] = "eligible";
        return doc;
    }

    private static EvidenceRegister Read(byte[] bytes)
    {
        var result = EvidenceRegister.Parse(bytes);
        Assert.True(result.Accepted, result.Reason?.ToString());
        return result.Value!;
    }

    private static JsonObject Document() => (JsonObject)JsonNode.Parse(Seed)!;

    private static string RepoRoot()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
            if (File.Exists(Path.Combine(dir.FullName, "delivery", "evidence", "register-v1.json")))
                return dir.FullName;
        throw new DirectoryNotFoundException("repository root");
    }
    private static byte[] Bytes(JsonNode doc) => Encoding.UTF8.GetBytes(doc.ToJsonString());
    private static bool SchemaAccepts(byte[] bytes) => Schema.Evaluate(JsonNode.Parse(bytes),
        new EvaluationOptions { RequireFormatValidation = true }).IsValid;
}
