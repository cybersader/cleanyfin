using System.Text;
using Cleanyfin.Delivery;

namespace Cleanyfin.Marking;

/// <summary>
/// The household's marking record: append-only captures, every mark revision, and the hash-chained audit log.
/// In-memory and plain-file implementations live here; the household plugin implements the same interface behind its
/// endpoints (R16, slice 2: <c>PluginMarkingStore</c>). A separate compute service (AI suggestions, fingerprinting, alignment, possibly on other
/// hardware) reaches the household only through <see cref="MarkingService.AddCapture"/> as a <c>suggester</c>:
/// it can add suggested captures and never accept or publish.
/// </summary>
public interface IMarkingStore
{
    /// <summary>Appends one capture. Refs are unique; captures are never edited.</summary>
    void AppendCapture(Capture capture);
    Capture? FindCapture(string captureRef);
    IReadOnlyList<Capture> Captures();

    /// <summary>
    /// Stores one mark revision: rev 1 for a new ref, the next rev for an edit, or the same rev when only its
    /// state changes. Earlier revisions are kept.
    /// </summary>
    void PutMark(Mark mark);
    /// <summary>The given revision, or the latest when <paramref name="rev"/> is null.</summary>
    Mark? FindMark(string markRef, int? rev = null);
    IReadOnlyList<Mark> LatestMarks();

    /// <summary>Appends one chained audit event; the store assigns seq and prev.</summary>
    AuditEvent AppendAudit(DateTimeOffset at, string actor, string type, string subject, string? digest);
    IReadOnlyList<string> AuditLines();
}

/// <summary>Plans this household issued, so a capture on a filtered clock can be mapped back to source time.</summary>
public interface IIssuedPlanLookup
{
    PlaybackPlan? FindIssuedPlan(string planRev);
}

public class InMemoryMarkingStore : IMarkingStore, IIssuedPlanLookup
{
    private readonly List<Capture> _captures = [];
    private readonly Dictionary<string, List<Mark>> _marks = new(StringComparer.Ordinal);
    private readonly List<string> _markOrder = [];
    private readonly List<string> _audit = [];
    private readonly Dictionary<string, PlaybackPlan> _plans = new(StringComparer.Ordinal);
    protected readonly object Gate = new();

    public void RegisterIssuedPlan(PlaybackPlan plan)
    {
        lock (Gate)
            _plans[plan.Revision] = plan;
    }

    public PlaybackPlan? FindIssuedPlan(string planRev)
    {
        lock (Gate)
            return _plans.GetValueOrDefault(planRev);
    }

    public virtual void AppendCapture(Capture capture)
    {
        lock (Gate)
        {
            if (_captures.Any(c => c.Ref == capture.Ref))
                throw new MarkingRejected(MarkingRefusal.DuplicateReference);
            _captures.Add(capture);
        }
    }

    public Capture? FindCapture(string captureRef)
    {
        lock (Gate)
            return _captures.FirstOrDefault(c => c.Ref == captureRef);
    }

    public IReadOnlyList<Capture> Captures()
    {
        lock (Gate)
            return _captures.ToArray();
    }

    public virtual void PutMark(Mark mark)
    {
        lock (Gate)
        {
            MarkingDocuments.CheckMark(mark);
            PutMarkUnlocked(mark);
        }
    }

    public Mark? FindMark(string markRef, int? rev = null)
    {
        lock (Gate)
        {
            if (!_marks.TryGetValue(markRef, out var revisions))
                return null;
            return rev is null ? revisions[^1] : revisions.FirstOrDefault(m => m.Rev == rev);
        }
    }

    public IReadOnlyList<Mark> LatestMarks()
    {
        lock (Gate)
            return _markOrder.Select(r => _marks[r][^1]).ToArray();
    }

    public virtual AuditEvent AppendAudit(DateTimeOffset at, string actor, string type, string subject, string? digest)
    {
        lock (Gate)
        {
            var (e, line) = AuditLog.Next(_audit.Count == 0 ? null : _audit[^1], _audit.Count, at, actor, type, subject, digest);
            _audit.Add(line);
            OnAuditLine(line);
            return e;
        }
    }

    public IReadOnlyList<string> AuditLines()
    {
        lock (Gate)
            return _audit.ToArray();
    }

    protected virtual void OnAuditLine(string line) { }

    /// <summary>Loads previously persisted state without re-chaining it. Used by <see cref="FileMarkingStore"/>.</summary>
    protected void Load(IEnumerable<Capture> captures, IEnumerable<Mark> markLines, IEnumerable<string> auditLines)
    {
        foreach (var c in captures)
            AppendCaptureCore(c);
        foreach (var m in markLines)
            PutMarkCore(m);
        _audit.AddRange(auditLines);
    }

    private void AppendCaptureCore(Capture c)
    {
        if (_captures.Any(x => x.Ref == c.Ref))
            throw new InvalidDataException("Duplicate capture ref in the store.");
        _captures.Add(c);
    }

    private void PutMarkCore(Mark m)
    {
        try { PutMarkUnlocked(m); }
        catch (MarkingRejected) { throw new InvalidDataException("Mark revisions in the store are out of order."); }
    }

    private static bool SameContent(Mark a, Mark b) =>
        MarkingDocuments.WriteMark(a with { State = "proposed" }).AsSpan().SequenceEqual(MarkingDocuments.WriteMark(b with { State = "proposed" }));

    private void PutMarkUnlocked(Mark mark)
    {
        if (!_marks.TryGetValue(mark.Ref, out var revisions))
        {
            if (mark.Rev != 1)
                throw new MarkingRejected(MarkingRefusal.StaleRevision);
            _marks[mark.Ref] = [mark];
            _markOrder.Add(mark.Ref);
            return;
        }
        var latest = revisions[^1];
        if (mark.Rev == latest.Rev + 1)
            revisions.Add(mark);
        else if (mark.Rev == latest.Rev && SameContent(mark, latest))
            revisions[^1] = mark;
        else
            throw new MarkingRejected(MarkingRefusal.StaleRevision);
    }
}

/// <summary>
/// Three JSON Lines files in one private directory: <c>captures.jsonl</c>, <c>marks.jsonl</c> (every revision and state
/// change, latest line wins) and <c>audit.jsonl</c>. Everything is re-read strictly on open. For the CLI and the
/// disposable lab; the plugin's store (slice 2) adds a recorded audit head behind the same interface.
/// </summary>
public sealed class FileMarkingStore : InMemoryMarkingStore
{
    private readonly string _captures, _marks, _audit;

    public FileMarkingStore(string directory)
    {
        Directory.CreateDirectory(directory);
        _captures = Path.Combine(directory, "captures.jsonl");
        _marks = Path.Combine(directory, "marks.jsonl");
        _audit = Path.Combine(directory, "audit.jsonl");
        var audit = ReadLines(_audit);
        var verified = AuditLog.Verify(audit);
        if (!verified.Accepted)
            throw new InvalidDataException($"audit.jsonl chain is broken at line {verified.Line}.");
        Load(ReadLines(_captures).Select(l => Require(MarkingDocuments.ParseCapture(Encoding.UTF8.GetBytes(l)))),
            ReadLines(_marks).Select(l => Require(MarkingDocuments.ParseMark(Encoding.UTF8.GetBytes(l)))), audit);
    }

    public override void AppendCapture(Capture capture)
    {
        lock (Gate)
        {
            base.AppendCapture(capture);
            AppendLine(_captures, Encoding.UTF8.GetString(MarkingDocuments.WriteCapture(capture)));
        }
    }

    public override void PutMark(Mark mark)
    {
        lock (Gate)
        {
            base.PutMark(mark);
            AppendLine(_marks, Encoding.UTF8.GetString(MarkingDocuments.WriteMark(mark)));
        }
    }

    protected override void OnAuditLine(string line) => AppendLine(_audit, line);

    private static void AppendLine(string path, string line)
    {
        using var f = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read);
        f.Write(Encoding.UTF8.GetBytes(line + "\n"));
        f.Flush(true);
    }

    private static List<string> ReadLines(string path) =>
        File.Exists(path) ? File.ReadAllText(path, new UTF8Encoding(false, true)).Split('\n').Where(l => l.Length > 0).ToList() : [];

    private static T Require<T>(MarkingResult<T> result) where T : class =>
        result.Value ?? throw new InvalidDataException($"Unreadable store line: {result.Reason}.");
}
