using System.Collections.Immutable;
using System.Text;
using System.Text.Json;
using Cleanyfin.Delivery;
using static Cleanyfin.Marking.StrictJson;

namespace Cleanyfin.Marking;

/// <summary>Strict readers and canonical writers for the marking documents. Parsing grants no authority.</summary>
public static class MarkingDocuments
{
    private static readonly string[] Surfaces = ["phone-remote", "review-page", "hotkey", "service"];
    private static readonly string[] CaptureCategories = [.. CoreCategories.Quick, .. CoreCategories.All];
    private static readonly string[] Actions = ["skip", "cut", "mute", "mark"];
    private static readonly string[] States = ["proposed", "accepted", "rejected", "withdrawn"];
    private static readonly string[] Methods = ["live", "refined", "import:mcf", "import:edl", "auto:subtitle", "auto:service"];
    private static readonly JsonWriterOptions Compact = new() { Indented = false };
    private static readonly JsonWriterOptions Indented = new() { Indented = true };

    // ---------- capture-v1 ----------

    public static MarkingResult<Capture> ParseCapture(ReadOnlyMemory<byte> utf8) => Parse(utf8, ReadCapture);

    /// <summary>A PWA export: a JSON array of capture-v1 objects.</summary>
    public static MarkingResult<CaptureExport> ParseCaptureExport(ReadOnlyMemory<byte> utf8) => Parse(utf8, root =>
    {
        var captures = Array(root, 4096).Select(ReadCapture).ToImmutableArray();
        Check(captures.Select(c => c.Ref).Distinct(StringComparer.Ordinal).Count() == captures.Length, MarkingRefusal.DuplicateReference);
        return new CaptureExport(captures);
    });

    internal static Capture ReadCapture(JsonElement root)
    {
        var f = Object(root, "captureVersion", "ref", "capturedAt", "author", "surface", "target", "clock", "kind", "atMs",
            "?endAtMs", "window", "?observed", "?category", "?note");
        Check(Integer(f["captureVersion"]) == 1, MarkingRefusal.UnsupportedVersion);
        var target = Object(f["target"], "serverRef", "itemId", "mediaSourceId");
        var clockFields = Object(f["clock"], "kind", "?planRev");
        var clockKind = OneOf(clockFields["kind"], ["source", "filtered"]);
        var planRev = Optional(clockFields, "planRev") is { } p ? Reference(p) : null;
        Check(clockKind == "source" ? planRev is null : planRev is not null, clockKind == "source"
            ? MarkingRefusal.UnknownField : MarkingRefusal.MissingField);
        var kind = OneOf(f["kind"], ["point", "span"]);
        var at = Integer(f["atMs"], 0);
        long? end = Optional(f, "endAtMs") is { } e ? Integer(e, 1) : null;
        Check(kind == "span" ? end is not null : end is null, kind == "span" ? MarkingRefusal.MissingField : MarkingRefusal.UnknownField);
        Check(end is null || end > at, MarkingRefusal.InvalidInterval);
        var window = Object(f["window"], "beforeMs", "afterMs");
        ObservedPosition? observed = null;
        if (Optional(f, "observed") is { } o)
        {
            var of = Object(o, "sessionPositionMs", "reportedAt", "paused");
            observed = new(Integer(of["sessionPositionMs"], 0), Timestamp(of["reportedAt"]), Boolean(of["paused"]));
        }
        return new Capture(Reference(f["ref"]), Timestamp(f["capturedAt"]), Reference(f["author"]), OneOf(f["surface"], Surfaces),
            new(Reference(target["serverRef"]), Reference(target["itemId"]), Reference(target["mediaSourceId"])),
            new(clockKind, planRev), kind, at, end,
            new(Integer(window["beforeMs"], 0, 600_000), Integer(window["afterMs"], 0, 600_000)), observed,
            Optional(f, "category") is { } c ? OneOf(c, CaptureCategories, MarkingRefusal.UnknownCategory) : null,
            Optional(f, "note") is { } n ? Text(n, 500) : null);
    }

    public static byte[] WriteCapture(Capture c) => Write(Compact, w => WriteCapture(w, c));

    private static void WriteCapture(Utf8JsonWriter w, Capture c)
    {
        w.WriteStartObject();
        w.WriteNumber("captureVersion", 1);
        w.WriteString("ref", c.Ref);
        w.WriteString("capturedAt", FormatUtc(c.CapturedAt));
        w.WriteString("author", c.Author);
        w.WriteString("surface", c.Surface);
        w.WriteStartObject("target");
        w.WriteString("serverRef", c.Target.ServerRef);
        w.WriteString("itemId", c.Target.ItemId);
        w.WriteString("mediaSourceId", c.Target.MediaSourceId);
        w.WriteEndObject();
        w.WriteStartObject("clock");
        w.WriteString("kind", c.Clock.Kind);
        if (c.Clock.PlanRev is { } plan)
            w.WriteString("planRev", plan);
        w.WriteEndObject();
        w.WriteString("kind", c.Kind);
        w.WriteNumber("atMs", c.AtMs);
        if (c.EndAtMs is { } end)
            w.WriteNumber("endAtMs", end);
        w.WriteStartObject("window");
        w.WriteNumber("beforeMs", c.Window.BeforeMs);
        w.WriteNumber("afterMs", c.Window.AfterMs);
        w.WriteEndObject();
        if (c.Observed is { } o)
        {
            w.WriteStartObject("observed");
            w.WriteNumber("sessionPositionMs", o.SessionPositionMs);
            w.WriteString("reportedAt", FormatUtc(o.ReportedAt));
            w.WriteBoolean("paused", o.Paused);
            w.WriteEndObject();
        }
        if (c.Category is { } category)
            w.WriteString("category", category);
        if (c.Note is { } note)
            w.WriteString("note", note);
        w.WriteEndObject();
    }

    // ---------- mark-v1 ----------

    public static MarkingResult<Mark> ParseMark(ReadOnlyMemory<byte> utf8) => Parse(utf8, ReadMark);

    internal static Mark ReadMark(JsonElement root)
    {
        var f = Object(root, "markVersion", "ref", "rev", "bindingRev", "startMs", "endMs", "boundsSource", "?category",
            "?subcategory", "?severity", "?action", "state", "provenance", "editedBy", "?contentSha256");
        Check(Integer(f["markVersion"]) == 1, MarkingRefusal.UnsupportedVersion);
        var p = Object(f["provenance"], "from", "method", "by", "license");
        var from = Array(p["from"], 64).Select(Reference).ToImmutableArray();
        Check(from.Distinct(StringComparer.Ordinal).Count() == from.Length, MarkingRefusal.DuplicateReference);
        string? subcategory = null;
        if (Optional(f, "subcategory") is { } s)
        {
            subcategory = Text(s, 128);
            Check(IsConceptId(subcategory), MarkingRefusal.UnknownConcept);
        }
        var mark = new Mark(Reference(f["ref"]), (int)Integer(f["rev"], 1, int.MaxValue), Reference(f["bindingRev"]),
            Integer(f["startMs"], 0), Integer(f["endMs"], 1), OneOf(f["boundsSource"], [Mark.CaptureWindowBounds, Mark.ReviewerBounds]),
            Optional(f, "category") is { } c ? OneOf(c, CoreCategories.All, MarkingRefusal.UnknownCategory) : null, subcategory,
            Optional(f, "severity") is { } sev ? (int)Integer(sev, 0, 3) : null,
            Optional(f, "action") is { } a ? OneOf(a, Actions) : null, OneOf(f["state"], States),
            new(from, OneOf(p["method"], Methods), Reference(p["by"]), Reference(p["license"])), Reference(f["editedBy"]),
            Optional(f, "contentSha256") is { } d ? Digest(d) : null);
        CheckMark(mark);
        return mark;
    }

    internal static void CheckMark(Mark m)
    {
        Check(m.StartMs >= 0 && m.StartMs < m.EndMs, MarkingRefusal.InvalidInterval);
        int present = new object?[] { m.Category, m.Severity, m.Action, m.ContentSha256 }.Count(x => x is not null);
        Check(present is 0 or 4, MarkingRefusal.IncompleteMark);
        Check(m.ContentSha256 == m.ComputeContentSha256(), MarkingRefusal.DigestMismatch);
        Check(m.State != "accepted" || (m.IsComplete && m.BoundsSource == Mark.ReviewerBounds), MarkingRefusal.IncompleteMark);
    }

    public static byte[] WriteMark(Mark m) => Write(Compact, w =>
    {
        w.WriteStartObject();
        w.WriteNumber("markVersion", 1);
        w.WriteString("ref", m.Ref);
        w.WriteNumber("rev", m.Rev);
        w.WriteString("bindingRev", m.BindingRev);
        w.WriteNumber("startMs", m.StartMs);
        w.WriteNumber("endMs", m.EndMs);
        w.WriteString("boundsSource", m.BoundsSource);
        if (m.Category is { } c)
            w.WriteString("category", c);
        if (m.Subcategory is { } s)
            w.WriteString("subcategory", s);
        if (m.Severity is { } sev)
            w.WriteNumber("severity", sev);
        if (m.Action is { } a)
            w.WriteString("action", a);
        w.WriteString("state", m.State);
        w.WriteStartObject("provenance");
        w.WriteStartArray("from");
        foreach (var r in m.Provenance.From)
            w.WriteStringValue(r);
        w.WriteEndArray();
        w.WriteString("method", m.Provenance.Method);
        w.WriteString("by", m.Provenance.By);
        w.WriteString("license", m.Provenance.License);
        w.WriteEndObject();
        w.WriteString("editedBy", m.EditedBy);
        if (m.ContentSha256 is { } d)
            w.WriteString("contentSha256", d);
        w.WriteEndObject();
    });

    // ---------- taxonomy-v1 ----------

    public static MarkingResult<Taxonomy> ParseTaxonomy(ReadOnlyMemory<byte> utf8) => Parse(utf8, root =>
    {
        var f = Object(root, "taxonomyVersion", "rev", "concepts");
        Check(Integer(f["taxonomyVersion"]) == 1, MarkingRefusal.UnsupportedVersion);
        var concepts = Array(f["concepts"], 512).Select(item =>
        {
            var c = Object(item, "id", "label", "broader", "?defaultSeverity", "?defaultAction", "?deprecated");
            var id = Text(c["id"], 128);
            Check(IsConceptId(id), MarkingRefusal.UnknownConcept);
            var broader = Text(c["broader"], 128);
            Check(CoreCategories.IsCore(broader) || IsConceptId(broader), MarkingRefusal.UnknownConcept);
            return new TaxonomyConcept(id, Text(c["label"], 200), broader,
                Optional(c, "defaultSeverity") is { } s ? (int)Integer(s, 0, 3) : null,
                Optional(c, "defaultAction") is { } a ? OneOf(a, Actions) : null,
                Optional(c, "deprecated") is { } d && Boolean(d));
        }).ToImmutableArray();
        Check(concepts.Select(c => c.Id).Distinct(StringComparer.Ordinal).Count() == concepts.Length, MarkingRefusal.DuplicateReference);
        var taxonomy = new Taxonomy(Reference(f["rev"]), concepts);
        foreach (var concept in concepts)
        {
            var rolled = taxonomy.RollUp(concept.Id);
            Check(rolled.Accepted, rolled.Reason ?? MarkingRefusal.UnknownConcept);
        }
        return taxonomy;
    });

    // ---------- household-governance-v1 ----------

    public static MarkingResult<Governance> ParseGovernance(ReadOnlyMemory<byte> utf8) => Parse(utf8, root =>
    {
        var f = Object(root, "governanceVersion", "rev", "members", "profiles", "approval");
        Check(Integer(f["governanceVersion"]) == 1, MarkingRefusal.UnsupportedVersion);
        var profiles = Array(f["profiles"], 256).Select(item =>
        {
            var p = Object(item, "profileRef", "floor", "viewerMay");
            var floor = Array(p["floor"], 9).Select(ReadRule).ToImmutableArray();
            Check(floor.Select(r => r.Category).Distinct().Count() == floor.Length, MarkingRefusal.ConflictingInput);
            return new ProfileFloor(Reference(p["profileRef"]), floor, OneOf(p["viewerMay"], ["tighten-only", "none"]));
        }).ToImmutableArray();
        Check(profiles.Select(p => p.ProfileRef).Distinct().Count() == profiles.Length, MarkingRefusal.DuplicateReference);
        var members = Array(f["members"], 256).Select(item =>
        {
            var m = Object(item, "user", "roles", "?profileRef");
            var roles = Array(m["roles"], 5, 1).Select(r => OneOf(r, Roles.All)).ToImmutableArray();
            Check(roles.Distinct().Count() == roles.Length, MarkingRefusal.DuplicateReference);
            // A compute service may only suggest; it can never hold a role that approves or publishes.
            Check(!roles.Contains(Roles.Suggester) || roles.Length == 1, MarkingRefusal.ConflictingInput);
            var profile = Optional(m, "profileRef") is { } pr ? Reference(pr) : null;
            Check(profile is null || profiles.Any(p => p.ProfileRef == profile), MarkingRefusal.UnknownProfile);
            return new Member(Reference(m["user"]), roles, profile);
        }).ToImmutableArray();
        Check(members.Select(m => m.User).Distinct().Count() == members.Length, MarkingRefusal.DuplicateReference);
        var a = Object(f["approval"], "selfApproval", "snapshotExpiryDays");
        return new Governance(Reference(f["rev"]), members, profiles,
            new(Boolean(a["selfApproval"]), (int)Integer(a["snapshotExpiryDays"], 1, 3650)));
    });

    private static CategoryRule ReadRule(JsonElement item)
    {
        var r = Object(item, "category", "minimumSeverity", "filter");
        return new CategoryRule(OneOf(r["category"], CoreCategories.All, MarkingRefusal.UnknownCategory),
            (int)Integer(r["minimumSeverity"], 0, 3), Boolean(r["filter"]));
    }

    // ---------- audit-event-v1 ----------

    public static MarkingResult<AuditEvent> ParseAuditEvent(ReadOnlyMemory<byte> utf8) => Parse(utf8, root =>
    {
        var f = Object(root, "eventVersion", "seq", "at", "actor", "type", "subject", "?digest", "prev");
        Check(Integer(f["eventVersion"]) == 1, MarkingRefusal.UnsupportedVersion);
        var subject = Text(f["subject"], 270);
        Check(IsSubject(subject), MarkingRefusal.InvalidValue);
        return new AuditEvent(Integer(f["seq"], 1), Timestamp(f["at"]), Reference(f["actor"]), OneOf(f["type"], AuditTypes.All),
            subject, Optional(f, "digest") is { } d ? Digest(d) : null, Digest(f["prev"]));
    });

    internal static bool IsSubject(string subject)
    {
        int at = subject.IndexOf('@');
        if (at < 0)
            return IsReference(subject);
        var rev = subject[(at + 1)..];
        return IsReference(subject[..at]) && rev.Length is > 0 and <= 10 && rev[0] != '0' && rev.All(char.IsAsciiDigit);
    }

    public static byte[] WriteAuditEvent(AuditEvent e) => Write(Compact, w =>
    {
        w.WriteStartObject();
        w.WriteNumber("eventVersion", 1);
        w.WriteNumber("seq", e.Seq);
        w.WriteString("at", FormatUtc(e.At));
        w.WriteString("actor", e.Actor);
        w.WriteString("type", e.Type);
        w.WriteString("subject", e.Subject);
        if (e.Digest is { } d)
            w.WriteString("digest", d);
        w.WriteString("prev", e.Prev);
        w.WriteEndObject();
    });

    // ---------- reviewer decisions (CLI input) ----------

    /// <summary>
    /// <c>{"decisionsVersion":1,"marks":[{"capture","mark","startMs","endMs","category"?,"subcategory"?,"severity","action"}]}</c>.
    /// Category may be omitted when a subcategory names it, or when the capture's quick hint names exactly one core category.
    /// </summary>
    public static MarkingResult<ReviewerDecisions> ParseDecisions(ReadOnlyMemory<byte> utf8) => Parse(utf8, root =>
    {
        var f = Object(root, "decisionsVersion", "marks");
        Check(Integer(f["decisionsVersion"]) == 1, MarkingRefusal.UnsupportedVersion);
        var marks = Array(f["marks"], 4096).Select(item =>
        {
            var d = Object(item, "capture", "mark", "startMs", "endMs", "?category", "?subcategory", "severity", "action");
            var start = Integer(d["startMs"], 0);
            var end = Integer(d["endMs"], 1);
            Check(start < end, MarkingRefusal.InvalidInterval);
            return new ReviewerDecision(Reference(d["capture"]), Reference(d["mark"]), new MarkEdit(start, end,
                Optional(d, "category") is { } c ? OneOf(c, CoreCategories.All, MarkingRefusal.UnknownCategory) : null,
                Optional(d, "subcategory") is { } s ? Text(s, 128) : null,
                (int)Integer(d["severity"], 0, 3), OneOf(d["action"], Actions)));
        }).ToImmutableArray();
        Check(marks.Select(m => m.MarkRef).Distinct(StringComparer.Ordinal).Count() == marks.Length, MarkingRefusal.DuplicateReference);
        return new ReviewerDecisions(marks);
    });

    public static MarkingResult<HouseholdPolicy> ParsePolicy(ReadOnlyMemory<byte> utf8)
    {
        var parsed = ResolverInputReader.ParsePolicy(utf8);
        return parsed.Accepted ? MarkingResult<HouseholdPolicy>.Allow(parsed.Value!)
            : MarkingResult<HouseholdPolicy>.Refuse(MarkingRefusal.SnapshotInvalid, parsed.Reason);
    }

    // ---------- existing delivery documents ----------

    /// <summary>Writes annotation-snapshot-v1 in the same shape the hand-placed fixtures use.</summary>
    public static byte[] WriteSnapshot(AcceptedAnnotationSnapshot s) => Write(Indented, w =>
    {
        w.WriteStartObject();
        w.WriteNumber("snapshotVersion", 1);
        w.WriteString("snapshotRev", s.SnapshotRev);
        w.WriteString("coverage", s.Coverage);
        w.WriteString("acceptedAt", FormatUtc(s.AcceptedAt));
        w.WriteString("expiresAt", FormatUtc(s.ExpiresAt));
        w.WritePropertyName("binding");
        WriteBinding(w, s.Binding);
        w.WriteStartArray("annotations");
        foreach (var a in s.Annotations)
        {
            w.WriteStartObject();
            w.WriteString("ref", a.Ref);
            w.WriteString("contentSha256", a.ContentSha256);
            w.WriteString("category", a.Category);
            w.WriteNumber("severity", a.Severity);
            w.WriteString("action", a.Action);
            w.WriteNumber("startMs", a.StartMs);
            w.WriteNumber("endMs", a.EndMs);
            w.WriteEndObject();
        }
        w.WriteEndArray();
        w.WriteEndObject();
    });

    private static void WriteBinding(Utf8JsonWriter w, SourceBinding b)
    {
        w.WriteStartObject();
        w.WriteString("serverRef", b.ServerRef);
        w.WriteString("itemId", b.ItemId);
        w.WriteString("mediaSourceId", b.MediaSourceId);
        w.WriteString("sha256", b.Sha256);
        w.WriteNumber("sizeBytes", b.SizeBytes);
        w.WriteString("timelineRef", b.TimelineRef);
        w.WriteString("bindingRev", b.BindingRev);
        w.WriteNumber("durationMs", b.DurationMs);
        w.WriteString("origin", b.Origin);
        w.WriteStartArray("tracks");
        foreach (var t in b.Tracks)
        {
            w.WriteStartObject();
            w.WriteNumber("index", t.Index);
            w.WriteString("kind", t.Kind);
            w.WriteStartArray("timeBase");
            w.WriteNumberValue(t.TimeBaseNumerator);
            w.WriteNumberValue(t.TimeBaseDenominator);
            w.WriteEndArray();
            w.WriteNumber("anchorPts", t.AnchorPts);
            w.WriteNumber("anchorSourceMs", t.AnchorSourceMs);
            w.WriteEndObject();
        }
        w.WriteEndArray();
        w.WriteEndObject();
    }

    /// <summary>Writes household-policy-v1 exactly as the existing reader expects it.</summary>
    public static byte[] WritePolicy(HouseholdPolicy p) => Write(Indented, w =>
    {
        w.WriteStartObject();
        w.WriteNumber("policyVersion", 1);
        w.WriteString("profileRef", p.ProfileRef);
        w.WriteString("policyRev", p.PolicyRev);
        w.WriteString("trustRev", p.TrustRev);
        w.WriteStartArray("rules");
        foreach (var r in p.Rules)
        {
            w.WriteStartObject();
            w.WriteString("category", r.Category);
            w.WriteNumber("minimumSeverity", r.MinimumSeverity);
            w.WriteBoolean("filter", r.Filter);
            w.WriteEndObject();
        }
        w.WriteEndArray();
        w.WriteEndObject();
    });

    /// <summary>
    /// Reads a standalone binding object (the snapshot's <c>binding</c> shape) with the existing strict snapshot
    /// reader, so a binding the marking side accepts is exactly one the resolver accepts.
    /// </summary>
    public static MarkingResult<SourceBinding> ParseBinding(ReadOnlyMemory<byte> utf8)
    {
        if (utf8.IsEmpty || utf8.Length > MaxDocumentBytes)
            return MarkingResult<SourceBinding>.Refuse(utf8.IsEmpty ? MarkingRefusal.InvalidJson : MarkingRefusal.LimitExceeded);
        string raw;
        try
        {
            // Wrap only one complete JSON object, so the binding text cannot add snapshot fields of its own.
            using var doc = JsonDocument.Parse(utf8, new JsonDocumentOptions { MaxDepth = 6 });
            if (doc.RootElement.ValueKind != JsonValueKind.Object)
                return MarkingResult<SourceBinding>.Refuse(MarkingRefusal.InvalidValue);
            raw = doc.RootElement.GetRawText();
        }
        catch (JsonException)
        {
            return MarkingResult<SourceBinding>.Refuse(MarkingRefusal.InvalidJson);
        }
        var wrapper = new StringBuilder("{\"snapshotVersion\":1,\"snapshotRev\":\"binding:check\",\"coverage\":\"reviewed-marks-only\",")
            .Append("\"acceptedAt\":\"2000-01-01T00:00:00Z\",\"expiresAt\":\"2000-01-01T00:00:01Z\",\"binding\":")
            .Append(raw).Append(",\"annotations\":[]}");
        var parsed = ResolverInputReader.ParseSnapshot(Encoding.UTF8.GetBytes(wrapper.ToString()));
        return parsed.Accepted ? MarkingResult<SourceBinding>.Allow(parsed.Value!.Binding)
            : MarkingResult<SourceBinding>.Refuse(MarkingRefusal.SnapshotInvalid, parsed.Reason);
    }

    public static byte[] WriteBinding(SourceBinding b) => Write(Indented, w => WriteBinding(w, b));

    private static byte[] Write(JsonWriterOptions options, Action<Utf8JsonWriter> body)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, options))
            body(writer);
        return stream.ToArray();
    }
}
