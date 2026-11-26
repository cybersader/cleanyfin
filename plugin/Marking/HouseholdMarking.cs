using System;
using System.Collections.Generic;
using System.Collections.Immutable;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using Cleanyfin.Delivery;
using Cleanyfin.Marking;
using Jellyfin.Data;
using Jellyfin.Database.Implementations.Enums;
using Jellyfin.Plugin.Cleanyfin.AuthRange;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;

namespace Jellyfin.Plugin.Cleanyfin.Marking;

/// <summary>The store could not be read as written: a broken audit chain, a removed head, or an unreadable line.</summary>
internal sealed class StoreUnreadable(string reason, int? line = null) : Exception(reason)
{
    internal int? Line { get; } = line;
}

/// <summary>
/// The household marking record (R16) in one private directory under Jellyfin's plugin configurations folder:
/// <c>captures.jsonl</c>, <c>marks.jsonl</c> and the hash-chained <c>audit.jsonl</c>, plus <c>audit.head</c> (count and
/// digest of the newest line) so dropping the newest lines is evident too. Everything is re-read and re-verified when
/// the store is opened; the household service opens it for every request, so an edit on disk is noticed at once.
/// This is evidence of accidents, not protection against whoever controls the disk.
/// </summary>
internal sealed class PluginMarkingStore : InMemoryMarkingStore
{
    private readonly string _captures, _marks, _audit, _head;
    private long _count;

    internal PluginMarkingStore(string directory)
    {
        ShelfStore.NoLinks(directory);
        Directory.CreateDirectory(directory);
        _captures = Path.Combine(directory, "captures.jsonl");
        _marks = Path.Combine(directory, "marks.jsonl");
        _audit = Path.Combine(directory, "audit.jsonl");
        _head = Path.Combine(directory, "audit.head");
        var audit = ReadLines(_audit);
        var verified = AuditLog.Verify(audit);
        if (!verified.Accepted)
            throw new StoreUnreadable("AuditChainBroken", verified.Line);
        CheckHead(audit);
        _count = audit.Count;
        Load(ReadLines(_captures).Select(l => Require(MarkingDocuments.ParseCapture(Encoding.UTF8.GetBytes(l)))),
            ReadLines(_marks).Select(l => Require(MarkingDocuments.ParseMark(Encoding.UTF8.GetBytes(l)))), audit);
    }

    internal static string Head(IReadOnlyList<string> lines) => lines.Count == 0 ? AuditLog.Genesis : AuditLog.LineDigest(lines[^1]);

    private void CheckHead(List<string> audit)
    {
        ShelfStore.NoLinks(_head);
        if (!File.Exists(_head))
        {
            // A log without its recorded head means the head was removed; refuse rather than re-trust the log.
            if (audit.Count > 0)
                throw new StoreUnreadable("AuditHeadMissing");
            return;
        }
        var parts = File.ReadAllText(_head, new UTF8Encoding(false, true)).TrimEnd('\n').Split(' ');
        if (parts.Length != 2 || !long.TryParse(parts[0], NumberStyles.None, CultureInfo.InvariantCulture, out var count) ||
            count < 0 || count > audit.Count || (count == 0 ? AuditLog.Genesis : AuditLog.LineDigest(audit[(int)count - 1])) != parts[1])
            throw new StoreUnreadable("AuditTruncated", (int)Math.Min(audit.Count + 1, int.MaxValue));
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

    protected override void OnAuditLine(string line)
    {
        AppendLine(_audit, line);
        _count++;
        Atomic(_head, Encoding.UTF8.GetBytes(FormattableString.Invariant($"{_count} {AuditLog.LineDigest(line)}\n")));
    }

    private static void AppendLine(string path, string line)
    {
        ShelfStore.NoLinks(path);
        using var f = new FileStream(path, FileMode.Append, FileAccess.Write, FileShare.Read);
        f.Write(Encoding.UTF8.GetBytes(line + "\n"));
        f.Flush(true);
    }

    internal static void Atomic(string path, byte[] bytes)
    {
        ShelfStore.NoLinks(path);
        var tmp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        var created = false;
        try
        {
            using (var f = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                created = true;
                f.Write(bytes);
                f.Flush(true);
            }
            File.Move(tmp, path, true);
        }
        finally
        {
            if (created && File.Exists(tmp))
                File.Delete(tmp);
        }
    }

    internal static List<string> ReadLines(string path)
    {
        ShelfStore.NoLinks(path);
        try
        {
            return File.Exists(path)
                ? File.ReadAllText(path, new UTF8Encoding(false, true)).Split('\n').Where(l => l.Length > 0).ToList()
                : [];
        }
        catch (DecoderFallbackException)
        {
            throw new StoreUnreadable("UnreadableLine");
        }
    }

    private static T Require<T>(MarkingResult<T> result) where T : class =>
        result.Value ?? throw new StoreUnreadable("UnreadableLine");
}

/// <summary>One request's view of the household: a freshly opened store, the governance in force and the service.</summary>
internal sealed record MarkingSession(PluginMarkingStore Store, Governance Governance, string GovernanceSource,
    MarkingService Service, string Actor, ImmutableArray<string> Roles);

/// <summary>
/// The household marking service inside the plugin. Identity is the authenticated Jellyfin user. Default roles come from
/// Jellyfin's administrator flag (administrators are admin + reviewer; everyone else is a viewer who may flag a moment);
/// once <c>governance.json</c> exists it is authoritative. Every request runs under one lock against a store reopened
/// and re-verified from disk.
/// </summary>
internal sealed class HouseholdMarking(IUserManager users, ILibraryManager library, IServerApplicationHost host, Editions editions)
{
    internal const string Directory = "cleanyfin-marking-v1";
    private static readonly object Lock = new();

    internal static string Root => Path.Combine(Plugin.Instance!.ConfigurationsPath, Directory);
    // A private, atomically replaced household policy flag. Absence means off; corruption never enables it.
    private static string TestFlag => Path.Combine(Root, "test-playback-v1.flag");
    internal static bool TestPlayback => TestPlaybackRevision is not null;
    internal static Guid? TestPlaybackRevision
    {
        get
        {
            ShelfStore.NoLinks(TestFlag);
            try
            {
                using var f = File.OpenRead(TestFlag);
                if (f.Length != 32) return null;
                var b = new byte[32]; f.ReadExactly(b);
                return Guid.TryParseExact(Encoding.ASCII.GetString(b), "N", out var rev) && rev != Guid.Empty ? rev : null;
            }
            catch (FileNotFoundException) { return null; }
            catch (DirectoryNotFoundException) { return null; }
        }
    }
    internal static void SetTestPlayback(bool enabled)
    {
        ShelfStore.NoLinks(TestFlag);
        if (!enabled) { File.Delete(TestFlag); return; }
        if (TestPlayback) return;
        var temp = TestFlag + "." + Guid.NewGuid().ToString("N");
        try { using (var f = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            { f.Write(Encoding.ASCII.GetBytes(Guid.NewGuid().ToString("N"))); f.Flush(true); } File.Move(temp, TestFlag, true); }
        finally { File.Delete(temp); }
    }

    internal string ServerRef => host.SystemId;
    internal string ServerVersion => host.ApplicationVersionString;

    internal static string UserRef(Guid id) => "jf:user:" + id.ToString("N");

    internal T Use<T>(Guid user, Func<MarkingSession, T> body)
    {
        lock (Lock)
        {
            var store = new PluginMarkingStore(Root);
            var (governance, source) = LoadGovernance();
            var taxonomy = LoadTaxonomy();
            var actor = UserRef(user);
            var roles = governance.Find(actor)?.Roles ?? [];
            return body(new(store, governance, source, new MarkingService(store, governance, taxonomy, TimeProvider.System, store),
                actor, roles));
        }
    }

    private (Governance Governance, string Source) LoadGovernance()
    {
        var path = Path.Combine(Root, "governance.json");
        ShelfStore.NoLinks(path);
        if (File.Exists(path))
        {
            var parsed = MarkingDocuments.ParseGovernance(ReadBounded(path));
            return (parsed.Value ?? throw new StoreUnreadable("GovernanceInvalid"), "file");
        }
        var members = users.GetUsers().OrderBy(u => u.Id).Select(u => new Member(UserRef(u.Id),
            u.HasPermission(PermissionKind.IsAdministrator) ? [Roles.Admin, Roles.Reviewer] : [Roles.Viewer])).ToImmutableArray();
        var digest = Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(
            string.Join('\n', members.Select(m => m.User + "=" + string.Join(',', m.Roles))))))[..16];
        return (new Governance("gov:default:" + digest, members, [], new ApprovalSettings(true, 365)), "jellyfin-default");
    }

    private static Taxonomy LoadTaxonomy()
    {
        var path = Path.Combine(Root, "taxonomy.json");
        ShelfStore.NoLinks(path);
        if (!File.Exists(path))
            return Taxonomy.Empty;
        return MarkingDocuments.ParseTaxonomy(ReadBounded(path)).Value ?? throw new StoreUnreadable("TaxonomyInvalid");
    }

    private static byte[] ReadBounded(string path)
    {
        using var s = File.OpenRead(path);
        if (s.Length is <= 0 or > 1024 * 1024)
            throw new StoreUnreadable("DocumentTooLarge");
        var b = new byte[s.Length];
        s.ReadExactly(b);
        return b;
    }

    /// <summary>The Jellyfin video a caller may see, or null. Shelf pointers are not marking targets.</summary>
    internal Video? Visible(string itemId, Guid user)
    {
        if (!GrantTable<Binding>.IsHandle(itemId) || users.GetUserById(user) is not { } u)
            return null;
        var item = library.GetItemById<Video>(Guid.ParseExact(itemId, "N"), u);
        return item != null && item.IsVisibleStandalone(u) && !Plugin.Instance!.IsShelfPath(item.Path) ? item : null;
    }

    internal bool IsFilteredPointer(string itemId) =>
        GrantTable<Binding>.IsHandle(itemId) && library.GetItemById(Guid.ParseExact(itemId, "N")) is Video v && Plugin.Instance!.IsShelfPath(v.Path);

    /// <summary>
    /// The exact edition a mark belongs to: full SHA-256 and stream layout of the file's bytes, valid while the file's
    /// change signals are unchanged (<see cref="Editions"/>). A household file is hashed once in the background; until
    /// then this refuses with <c>VerificationPending</c>, so no mark is ever bound to a file whose identity was guessed.
    /// </summary>
    internal SourceBinding Bind(Video video) => editions.Verify(video).Binding(host.SystemId);

    internal EditionStatus EditionStatus(Video video) => editions.Status(video);

    internal static string BindingPrefix(string itemId) => "bind:" + itemId + ":";

    /// <summary>Writes the published snapshot where the shelf resolver already reads it.</summary>
    internal static string WriteSnapshot(string itemId, byte[] bytes)
    {
        var dir = Path.Combine(Plugin.Instance!.ShelfRoot, "snapshots");
        ShelfStore.NoLinks(dir);
        System.IO.Directory.CreateDirectory(dir);
        var path = Path.Combine(dir, itemId + ".json");
        PluginMarkingStore.Atomic(path, bytes);
        return path;
    }

    /// <summary>The review-page timing register: an operator file in the marking folder, else the one built in.</summary>
    internal static byte[] TimingRegister()
    {
        var path = Path.Combine(Root, "review-timing-v1.json");
        ShelfStore.NoLinks(path);
        if (File.Exists(path))
            return ReadBounded(path);
        using var s = typeof(HouseholdMarking).Assembly.GetManifestResourceStream("Cleanyfin.Review.Timing.v1")!;
        using var m = new MemoryStream();
        s.CopyTo(m);
        return m.ToArray();
    }
}
