using static Cleanyfin.Marking.StrictJson;

namespace Cleanyfin.Marking;

/// <summary>An edit request: the revision the editor saw, and the exact values they set.</summary>
public sealed record EditRequest(int Rev, MarkEdit Edit);

/// <summary>A reviewer decision on exactly one revision.</summary>
public sealed record RevisionRequest(int Rev);

/// <summary>Turn a capture into a mark: a draft over its search window, or exact values placed by a person.</summary>
public sealed record DraftRequest(string CaptureRef, MarkEdit? Exact);

/// <summary>Publish options. Expiry defaults to the household's limit.</summary>
public sealed record PublishRequest(DateTimeOffset? ExpiresAt);

/// <summary>
/// Strict, closed request bodies for a household store's HTTP surface (slice 2). They carry values only: never an
/// actor, author, role or state. Who is acting always comes from the authenticated caller, so a body cannot claim
/// someone else's authority.
/// </summary>
public static class MarkingRequests
{
    private const int MaxRequestBytes = 16 * 1024;

    public static MarkingResult<EditRequest> ParseEdit(ReadOnlyMemory<byte> utf8) => Bounded(utf8, root =>
    {
        var f = Object(root, "rev", "startMs", "endMs", "?category", "?subcategory", "severity", "action");
        return new EditRequest((int)Integer(f["rev"], 1, int.MaxValue), ReadEdit(f));
    });

    public static MarkingResult<RevisionRequest> ParseRevision(ReadOnlyMemory<byte> utf8) => Bounded(utf8, root =>
        new RevisionRequest((int)Integer(Object(root, "rev")["rev"], 1, int.MaxValue)));

    public static MarkingResult<DraftRequest> ParseDraft(ReadOnlyMemory<byte> utf8) => Bounded(utf8, root =>
    {
        var f = Object(root, "captureRef", "?exact");
        MarkEdit? exact = null;
        if (Optional(f, "exact") is { } e)
            exact = ReadEdit(Object(e, "startMs", "endMs", "?category", "?subcategory", "severity", "action"));
        return new DraftRequest(Reference(f["captureRef"]), exact);
    });

    public static MarkingResult<PublishRequest> ParsePublish(ReadOnlyMemory<byte> utf8) => Bounded(utf8, root =>
    {
        var f = Object(root, "?expiresAt");
        return new PublishRequest(Optional(f, "expiresAt") is { } t ? Timestamp(t) : null);
    });

    private static MarkEdit ReadEdit(Dictionary<string, System.Text.Json.JsonElement> f) => new(
        Integer(f["startMs"], 0), Integer(f["endMs"], 1),
        Optional(f, "category") is { } c ? OneOf(c, CoreCategories.All, MarkingRefusal.UnknownCategory) : null,
        Optional(f, "subcategory") is { } s ? Text(s, 128, MarkingRefusal.UnknownConcept) : null,
        (int)Integer(f["severity"], 0, 3), OneOf(f["action"], ["skip", "cut", "mute", "mark"]));

    private static MarkingResult<T> Bounded<T>(ReadOnlyMemory<byte> utf8, Func<System.Text.Json.JsonElement, T> read) where T : class =>
        utf8.Length > MaxRequestBytes ? MarkingResult<T>.Refuse(MarkingRefusal.LimitExceeded) : Parse(utf8, read);
}
