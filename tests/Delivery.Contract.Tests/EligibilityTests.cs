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
        Assert.All(register.Rows.Take(5), row => Assert.Equal(EvidenceStatus.Refused, row.Status));
        // Common-format rows: only R23-passing Dolby layouts may be experimental, in desktop lab mpv only.
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
            var evidence = Path.Combine(RepoRoot(), row.EvidenceRef);
            Assert.True(File.Exists(evidence), row.EvidenceRef);
            if (row.Status == EvidenceStatus.Experimental)
            {
                Assert.Contains(row.Tuple.Source.AudioCodec, new[] { "ac3", "eac3" });
                Assert.Contains("24/24 positions passed", row.Observation);
                Assert.Contains("R23:", row.Observation);
                Assert.StartsWith("R23:", JsonNode.Parse(File.ReadAllBytes(evidence))!["admissionStandard"]!.GetValue<string>());
                Assert.Equal(EvidenceStatus.Experimental, register.Admit(row.Tuple, ["cut"], allowExperimental: true).Value!.Status);
            }
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
        var doc = Document();
        doc["rows"]![1]!["status"] = "experimental"; // synthetic lab grant, never an active reviewed row
        var register = Read(Bytes(doc));
        var measured = register.Rows[1].Tuple;
        Assert.Equal(EvidenceStatus.Experimental, register.Admit(measured, ["cut"], allowExperimental: true).Value!.Status);
        Assert.Equal(RefusalCode.EvidenceFeatureUncovered, register.Admit(measured, ["mute"], allowExperimental: true).Reason);
        var reviewed = Read(Seed);
        foreach (var row in reviewed.Rows.Take(2))
            Assert.Equal(RefusalCode.EvidenceRefused, reviewed.Admit(row.Tuple, ["cut"], allowExperimental: true).Reason);
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

    private static JsonObject R24()
    {
        var doc = EligibleTestDocument();
        doc["registerVersion"] = 2;
        var row = doc["rows"]![0]!;
        row["status"] = "experimental";
        row["tier"] = "test";
        row["tuple"]!["source"]!["audioCodec"] = "eac3";
        row["results"]!["keptSoundAtJoin"]!["outcome"] = "fail";
        row["gate"] = new JsonObject { ["positions"] = 24, ["allTakesMeasured"] = true, ["markedFrames"] = 0,
            ["markedSoundDetections"] = 0, ["positiveControlsPassed"] = true };
        row["seams"] = new JsonObject { ["silence"] = "none", ["pop"] = "above control", ["keptPicture"] = "intact",
            ["sync"] = "not measured", ["keptSoundLoss"] = "10 ms" };
        return doc;
    }

    private static bool TestSchemaAccepts(JsonNode doc) => JsonSchema.FromText(File.ReadAllText(Path.Combine(
        AppContext.BaseDirectory, "evidence-register-v2.schema.json"))).Evaluate(doc).IsValid;

    [Fact]
    public void TestAdmissionIsExactAndNeverFullEvenWithLabFlag()
    {
        var doc = R24();
        Assert.True(TestSchemaAccepts(doc));
        var register = EvidenceRegister.ParseTest(Bytes(doc)).Value!;
        var tuple = register.Rows.Single().Tuple;
        Assert.False(register.Admit(tuple, ["cut"]).Accepted);
        Assert.False(register.Admit(tuple, ["cut"], allowExperimental: true).Accepted);
        Assert.Equal("above control", register.AdmitTest(tuple, ["cut"]).Value!.TestSeams!.Pop);
        Assert.False(register.AdmitTest(tuple with { DeviceMode = "Physical TV" }, ["cut"]).Accepted);
        Assert.False(register.AdmitTest(tuple, ["mute"]).Accepted);
        Assert.False(EvidenceRegister.Parse(Bytes(doc)).Accepted);
        Assert.False(EvidenceRegister.ParseTest(Seed).Accepted);
        var legacy = Read(Bytes(EligibleTestDocument()));
        Assert.False(legacy.AdmitTest(legacy.Rows[0].Tuple, ["cut"]).Accepted);
    }

    [Theory]
    [InlineData("markedFrames", "1")]
    [InlineData("markedSoundDetections", "1")]
    [InlineData("positiveControlsPassed", "false")]
    [InlineData("allTakesMeasured", "false")]
    [InlineData("positions", "23")]
    public void IncompleteTestGateRejectsTheRow(string key, string value)
    {
        var doc = R24();
        doc["rows"]![0]!["gate"]![key] = JsonNode.Parse(value);
        Assert.False(TestSchemaAccepts(doc));
        Assert.False(EvidenceRegister.ParseTest(Bytes(doc)).Accepted);
    }

    [Theory]
    [InlineData("aac")]
    [InlineData("flac")]
    public void TestTierRejectsUnapprovedCodecs(string codec)
    {
        var doc = R24();
        doc["rows"]![0]!["tuple"]!["source"]!["audioCodec"] = codec;
        Assert.False(TestSchemaAccepts(doc));
        Assert.False(EvidenceRegister.ParseTest(Bytes(doc)).Accepted);
    }

    [Fact]
    public void SupplementalSeedConformsAndNamesExistingEvidence()
    {
        var root = RepoRoot();
        var bytes = File.ReadAllBytes(Path.Combine(root, "delivery/evidence/register-v2.json"));
        Assert.True(TestSchemaAccepts(JsonNode.Parse(bytes)!));
        var seed = EvidenceRegister.ParseTest(bytes);
        Assert.True(seed.Accepted);
        foreach (var row in seed.Value!.Rows)
        {
            Assert.True(File.Exists(Path.Combine(root, row.EvidenceRef)));
            Assert.True(seed.Value.AdmitTest(row.Tuple, ["cut"]).Accepted);
            Assert.False(seed.Value.Admit(row.Tuple, ["cut"], allowExperimental: true).Accepted);
            Assert.False(seed.Value.AdmitTest(row.Tuple with { DeviceMode = "Unmeasured output" }, ["cut"]).Accepted);
        }
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
