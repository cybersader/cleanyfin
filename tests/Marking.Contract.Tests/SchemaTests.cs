using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Cleanyfin.Marking;
using static Marking.Contract.Tests.MarkingTests;

namespace Marking.Contract.Tests;

[Trait("Category", "Marking")]
public class SchemaTests
{
    [Fact]
    public void FixturesAndWrittenDocumentsValidateAgainstTheNewSchemas()
    {
        Assert.True(SchemaAccepts("household-governance-v1.schema.json", Fixture("governance.v1.json")));
        Assert.True(SchemaAccepts("taxonomy-v1.schema.json", Fixture("taxonomy.v1.json")));
        foreach (var item in JsonNode.Parse(Fixture("pwa-export.captures.json"))!.AsArray())
            Assert.True(SchemaAccepts("capture-v1.schema.json", Bytes(item!)));
        foreach (var c in Phone)
            Assert.True(SchemaAccepts("capture-v1.schema.json", MarkingDocuments.WriteCapture(c)));
        var (service, store) = Household();
        var draft = Require(service.CaptureToDraftMark(Phone[0].Ref, MarkRef, Binding.BindingRev, Parent));
        Assert.True(SchemaAccepts("mark-v1.schema.json", MarkingDocuments.WriteMark(draft)));
        var refined = Require(service.Edit(MarkRef, 1, new MarkEdit(5480, 10520, null, "household:fistfight", 2, "skip"), Parent));
        Require(service.Accept(MarkRef, refined.Rev, Parent));
        Assert.True(SchemaAccepts("mark-v1.schema.json", MarkingDocuments.WriteMark(store.FindMark(MarkRef)!)));
    }

    [Theory]
    [InlineData("capture", "/captureVersion", "2", MarkingRefusal.UnsupportedVersion)]
    [InlineData("capture", "/surface", "\"tv\"", MarkingRefusal.InvalidValue)]
    [InlineData("capture", "/category", "\"Violence\"", MarkingRefusal.UnknownCategory)]
    [InlineData("capture", "/endAtMs", null, MarkingRefusal.MissingField)]
    [InlineData("capture", "/clock/kind", "\"filtered\"", MarkingRefusal.MissingField)]
    [InlineData("capture", "/window/beforeMs", "600001", MarkingRefusal.InvalidValue)]
    [InlineData("capture", "/observed/paused", "\"no\"", MarkingRefusal.InvalidValue)]
    [InlineData("capture", "/capturedAt", "\"2026-11-24T20:59:11+00:00\"", MarkingRefusal.InvalidTimestamp)]
    [InlineData("capture", "/status", "\"published\"", MarkingRefusal.UnknownField)]
    [InlineData("capture", "/author", "\"jf user\"", MarkingRefusal.InvalidValue)]
    [InlineData("governance", "/approval/selfApproval", "\"yes\"", MarkingRefusal.InvalidValue)]
    [InlineData("governance", "/members/0/roles/0", "\"owner\"", MarkingRefusal.InvalidValue)]
    [InlineData("governance", "/profiles/0/viewerMay", "\"loosen\"", MarkingRefusal.InvalidValue)]
    [InlineData("governance", "/profiles/0/floor/0/category", "\"other\"", MarkingRefusal.UnknownCategory)]
    [InlineData("taxonomy", "/concepts/0/id", "\"JumpScare\"", MarkingRefusal.UnknownConcept)]
    [InlineData("taxonomy", "/concepts/0/broader", "\"scary\"", MarkingRefusal.UnknownConcept)]
    [InlineData("taxonomy", "/concepts/0/defaultAction", "\"blur\"", MarkingRefusal.InvalidValue)]
    public void SchemaAndStrictReadersAgreeOnStructuralRefusals(string kind, string path, string? value, MarkingRefusal reason)
    {
        var (file, schema) = kind switch
        {
            "capture" => ("pwa-export.captures.json", "capture-v1.schema.json"),
            "governance" => ("governance.v1.json", "household-governance-v1.schema.json"),
            _ => ("taxonomy.v1.json", "taxonomy-v1.schema.json")
        };
        var root = JsonNode.Parse(Fixture(file))!;
        if (kind == "capture")
            root = root[0]!.DeepClone();
        Assert.True(SchemaAccepts(schema, Bytes(root)));
        var node = root;
        var parts = path[1..].Split('/');
        foreach (var part in parts[..^1])
            node = node is JsonArray ? node[int.Parse(part, CultureInfo.InvariantCulture)]! : node[part]!;
        if (value is null)
            node.AsObject().Remove(parts[^1]);
        else if (node is JsonArray array)
            array[int.Parse(parts[^1], CultureInfo.InvariantCulture)] = JsonNode.Parse(value);
        else
            node[parts[^1]] = JsonNode.Parse(value);
        var bytes = Bytes(root);
        Assert.False(SchemaAccepts(schema, bytes));
        MarkingRefusal? actual = kind switch
        {
            "capture" => MarkingDocuments.ParseCapture(bytes).Reason,
            "governance" => MarkingDocuments.ParseGovernance(bytes).Reason,
            _ => MarkingDocuments.ParseTaxonomy(bytes).Reason
        };
        Assert.Equal(reason, actual);
    }

    [Fact]
    public void ReadersRefuseWhatTheSchemaCannotSee()
    {
        var capture = Encoding.UTF8.GetString(MarkingDocuments.WriteCapture(Phone[0]));
        Assert.Equal(MarkingRefusal.DuplicateKey, MarkingDocuments.ParseCapture(Encoding.UTF8.GetBytes(
            capture.Replace("\"kind\":\"span\"", "\"kind\":\"span\",\"kin\\u0064\":\"span\""))).Reason);
        Assert.Equal(MarkingRefusal.InvalidValue, MarkingDocuments.ParseCapture(Encoding.UTF8.GetBytes(
            capture.Replace($"\"atMs\":{Phone[0].AtMs}", $"\"atMs\":{Phone[0].AtMs}.0"))).Reason);
        Assert.Equal(MarkingRefusal.InvalidInterval, MarkingDocuments.ParseCapture(Encoding.UTF8.GetBytes(
            capture.Replace($"\"endAtMs\":{Phone[0].EndAtMs}", $"\"endAtMs\":{Phone[0].AtMs}"))).Reason);
        var export = Encoding.UTF8.GetString(Fixture("pwa-export.captures.json"));
        Assert.Equal(MarkingRefusal.DuplicateReference, MarkingDocuments.ParseCaptureExport(Encoding.UTF8.GetBytes(
            export.Replace(Phone[1].Ref, Phone[0].Ref))).Reason);
        var governance = Encoding.UTF8.GetString(Fixture("governance.v1.json"));
        Assert.Equal(MarkingRefusal.DuplicateReference, MarkingDocuments.ParseGovernance(Encoding.UTF8.GetBytes(
            governance.Replace("jf:user:parent2", "jf:user:spouse"))).Reason);
        Assert.Equal(MarkingRefusal.UnknownProfile, MarkingDocuments.ParseGovernance(Encoding.UTF8.GetBytes(
            governance.Replace("\"profileRef\": \"local:p2\"}", "\"profileRef\": \"local:p9\"}"))).Reason);
        // A mark whose digest does not match its fields is refused, as in the snapshot reader.
        var mark = new Mark(MarkRef, 1, "binding:3", 1, 2, Mark.ReviewerBounds, "violence", null, 2, "skip", "proposed",
            new([], "refined", Parent, "household-private"), Parent, new string('a', 64));
        Assert.Equal(MarkingRefusal.DigestMismatch, MarkingDocuments.ParseMark(MarkingDocuments.WriteMark(mark)).Reason);
    }

    private static byte[] Bytes(JsonNode node) => JsonSerializer.SerializeToUtf8Bytes(node);
}
