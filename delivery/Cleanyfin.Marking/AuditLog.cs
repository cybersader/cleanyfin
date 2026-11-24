using System.Security.Cryptography;
using System.Text;

namespace Cleanyfin.Marking;

/// <summary>
/// Hash-chained JSON Lines log (audit-event-v1). Each line's <c>prev</c> is the SHA-256 of the previous line's exact
/// UTF-8 bytes. Edits, removed or reordered lines become evident; this is not protection against whoever owns the disk,
/// and dropping the newest lines is only evident against a head digest recorded elsewhere.
/// </summary>
public static class AuditLog
{
    public static readonly string Genesis = new('0', 64);

    public static string LineDigest(string line) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(line)));

    /// <summary>The next event after <paramref name="lastLine"/> (null for an empty log), and its canonical line.</summary>
    public static (AuditEvent Event, string Line) Next(string? lastLine, long lastSeq, DateTimeOffset at, string actor,
        string type, string subject, string? digest)
    {
        StrictJson.Check(StrictJson.IsReference(actor), MarkingRefusal.InvalidValue);
        StrictJson.Check(AuditTypes.All.Contains(type), MarkingRefusal.InvalidValue);
        StrictJson.Check(MarkingDocuments.IsSubject(subject), MarkingRefusal.InvalidValue);
        StrictJson.Check(digest is null || StrictJson.IsDigest(digest), MarkingRefusal.InvalidValue);
        var e = new AuditEvent(lastSeq + 1, at, actor, type, subject, digest, lastLine is null ? Genesis : LineDigest(lastLine));
        return (e, Encoding.UTF8.GetString(MarkingDocuments.WriteAuditEvent(e)));
    }

    /// <summary>Checks every line strictly and the whole chain. Refuses with the first failing 1-based line.</summary>
    public static MarkingResult<IReadOnlyList<AuditEvent>> Verify(IReadOnlyList<string> lines)
    {
        var events = new List<AuditEvent>(lines.Count);
        string? previous = null;
        for (int i = 0; i < lines.Count; i++)
        {
            var parsed = MarkingDocuments.ParseAuditEvent(Encoding.UTF8.GetBytes(lines[i]));
            if (!parsed.Accepted)
                return MarkingResult<IReadOnlyList<AuditEvent>>.Refuse(MarkingRefusal.AuditChainBroken, line: i + 1);
            var e = parsed.Value!;
            var expectedPrev = previous is null ? Genesis : LineDigest(previous);
            // Canonical form: a re-serialized line must be byte-identical, so no edit can hide in spelling or order.
            var canonical = Encoding.UTF8.GetString(MarkingDocuments.WriteAuditEvent(e));
            if (e.Seq != i + 1 || e.Prev != expectedPrev || canonical != lines[i] ||
                (i > 0 && e.At < events[^1].At))
                return MarkingResult<IReadOnlyList<AuditEvent>>.Refuse(MarkingRefusal.AuditChainBroken, line: i + 1);
            events.Add(e);
            previous = lines[i];
        }
        return MarkingResult<IReadOnlyList<AuditEvent>>.Allow(events);
    }
}
