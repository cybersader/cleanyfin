using MediaBrowser.Controller.Providers;
using MediaBrowser.Model.IO;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Cleanyfin.Delivery;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Controller;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Configuration;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

internal sealed record ShelfInput(string Device, string Generation, string Version, ResolvedPlaybackPlan Resolution, MediaSourceInfo Source);
internal sealed class ShelfStore(ILibraryManager library, IUserManager users, IServerApplicationHost host,
    GrantService grants, PinnedAsset assets, IProviderManager providers, IFileSystem fileSystem, ILogger<ShelfStore> log) : IPlanRevisionAllocator
{
    private const int Limit = 8;
    private readonly SemaphoreSlim _writer = new(1);
    private readonly object _clockLock = new();
    private DateTimeOffset _floor;
    internal string Root => Plugin.Instance!.ShelfRoot;
    private string Maps => Path.Combine(Root, "inventory.json");
    private string Shelf(string uid) => Path.Combine(Root, "shelves", uid);
    private static string Name(string uid) => "Playback samples " + uid;
    private string Pointer(ShelfRow r) => Path.Combine(Shelf(r.Viewer), "Playback sample " + r.Key + ".strm");
    public string AllocateRevision() => "plan:" + Guid.NewGuid().ToString("N");
    internal bool PointerPath(string? path) => path != null && path.StartsWith(Path.Combine(Root, "shelves") + "/", StringComparison.Ordinal) && path.EndsWith(".strm", StringComparison.Ordinal);
    internal bool Identified(Video item)
    {
        if (!PointerPath(item.Path)) return false;
        var name = Path.GetFileNameWithoutExtension(item.Path); const string prefix = "Playback sample ";
        if (!name.StartsWith(prefix, StringComparison.Ordinal)) return false;
        var key = name[prefix.Length..]; var uid = Path.GetFileName(Path.GetDirectoryName(item.Path));
        return GrantTable<Binding>.IsHandle(key) && GrantTable<Binding>.IsHandle(uid) && Path.GetDirectoryName(item.Path) == Shelf(uid!) &&
            (item.ShortcutPath?.EndsWith("/Cleanyfin/pointer/" + key, StringComparison.Ordinal) == true || Rows().Any(r => Pointer(r) == item.Path));
    }
    private static void Require(bool ok) => FixtureGate.Require(ok, RefusalCode.UnverifiedBinding);
    private static byte[] Bytes(string path)
    {
        NoLinks(path); using var s = File.OpenRead(path); Require(s.Length is > 0 and <= ResolverInputReader.MaxDocumentBytes);
        var b = new byte[s.Length]; s.ReadExactly(b); Require(s.ReadByte() == -1); return b;
    }
    // The tree is private to the operator/plugin. Reject even a mistaken symlink to media.
    private static void NoLinks(string path)
    {
        for (string? p = Path.GetFullPath(path); p != null; p = Path.GetDirectoryName(p)) Require(new FileInfo(p).LinkTarget == null);
    }
    private static string Digest(byte[] b) => Convert.ToHexStringLower(SHA256.HashData(b));
    private List<ShelfRow> Rows()
    {
        if (!File.Exists(Maps)) return [];
        var rows = JsonSerializer.Deserialize<List<ShelfRow>>(Bytes(Maps));
        Require(rows != null && ShelfRow.ValidInventory(rows, 2 * Limit * Limit));
        return rows!;
    }
    private static void Atomic(string path, byte[] bytes, bool replace)
    {
        NoLinks(path); var tmp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        var created = false; try { using (var f = new FileStream(tmp, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { created = true; f.Write(bytes); f.Flush(true); } File.Move(tmp, path, replace); }
        finally { if (created) File.Delete(tmp); }
    }
    private void Save(List<ShelfRow> rows) => Atomic(Maps, JsonSerializer.SerializeToUtf8Bytes(rows), true);
    private bool Owned(ShelfRow row) => File.Exists(Pointer(row)) && Encoding.UTF8.GetString(Bytes(Pointer(row))) == row.Locator + "\n";
    internal ShelfRow Lookup(Video v)
    {
        var row = Rows().SingleOrDefault(r => Pointer(r) == v.Path);
        Require(row != null && v.IsShortcut && v.ShortcutPath == row.Locator && Owned(row)); return row!;
    }
    internal ShelfInput Resolve(string uid, string item)
    {
        var config = FixtureGate.Config;
        Require(GrantTable<Binding>.IsHandle(uid) && GrantTable<Binding>.IsHandle(item));
        var profilePath = Path.Combine(Root, "profiles", uid, "policy.json");
        var devicePath = Path.Combine(Root, "profiles", uid, "device.txt");
        var snapshotPath = Path.Combine(Root, "snapshots", item + ".json");
        string Version() => PinnedAsset.FileVersion(profilePath) + PinnedAsset.FileVersion(snapshotPath) + PinnedAsset.FileVersion(devicePath);
        NoLinks(profilePath); NoLinks(snapshotPath); NoLinks(devicePath);
        var version = Version(); var p = Bytes(profilePath); var a = Bytes(snapshotPath); var d = Bytes(devicePath);
        var policy = ResolverInputReader.ParsePolicy(p); var snapshot = ResolverInputReader.ParseSnapshot(a);
        var reviewed = snapshot.Value ?? throw new Refused(snapshot.Reason!.Value);
        var privacy = policy.Value ?? throw new Refused(policy.Reason!.Value);
        var device = Encoding.UTF8.GetString(d).TrimEnd('\n', '\r'); Require(device.Length is > 0 and <= 128 && !device.Any(char.IsControl));
        var video = library.GetItemById(Guid.ParseExact(item, "N")) as Video ?? throw new Refused(RefusalCode.BindingMismatch);
        var (src, verified, assetVersion) = assets.Verify(video);
        Require(reviewed.Binding.Tracks.SequenceEqual(new TrackClock[] { new(0, "video", 1, 12800, 0, 0), new(1, "audio", 1, 48000, 0, 0) }));
        DateTimeOffset floor; lock (_clockLock) { var now = DateTimeOffset.UtcNow; FixtureGate.Require(now >= _floor, RefusalCode.ClockRollback); floor = _floor = now; }
        var result = HouseholdResolver.Resolve(reviewed, privacy, new(uid, uid, host.SystemId, verified, TimeProvider.System, floor, TimeSpan.FromSeconds(config.LabLifetimeSeconds)), this);
        var plan = result.Value ?? throw new Refused(result.Reason!.Value);
        // Re-read to reject a mixed read when the administrator atomically changes a document during resolution.
        Require(version == Version());
        return new(device, GrantTable<Binding>.Hash(Digest(p) + Digest(a) + Digest(d)), GrantTable<Binding>.Hash(version + assetVersion), plan, src);
    }
    internal (ShelfRow Row, ShelfInput Input) Current(Video pointer, Guid user, string device)
    {
        var row = Lookup(pointer); FixtureGate.Require(row.Viewer == user.ToString("N"), RefusalCode.AuthorityMismatch);
        var live = Resolve(row.Viewer, row.Item);
        FixtureGate.Require(row.Generation == live.Generation && row.Timeline == live.Resolution.TimelineKey && row.Source == live.Source.Id, RefusalCode.RevisionMismatch);
        FixtureGate.Require(live.Device == device, RefusalCode.AuthorityMismatch);
        return (row, live);
    }
    private static bool Overlap(string a, string b) { a = Path.TrimEndingDirectorySeparator(Path.GetFullPath(a)); b = Path.TrimEndingDirectorySeparator(Path.GetFullPath(b)); return a == b || a.StartsWith(b.TrimEnd('/') + "/", StringComparison.Ordinal) || b.StartsWith(a.TrimEnd('/') + "/", StringComparison.Ordinal); }
    private void GuardLocations()
    {
        NoLinks(Root);
        foreach (var f in library.GetVirtualFolders()) foreach (var location in f.Locations)
            {
                NoLinks(location);
                if (!Overlap(Root, location)) continue;
                var uid = Path.GetFileName(location);
                Require(GrantTable<Binding>.IsHandle(uid) && location == Shelf(uid) && f.Name == Name(uid) && f.Locations.Length == 1);
            }
    }
    private async Task EnsureShelf(string uid)
    {
        var path = Shelf(uid); NoLinks(path); Directory.CreateDirectory(path);
        var folders = library.GetVirtualFolders(); var f = folders.SingleOrDefault(f => f.Name == Name(uid));
        if (f != null) { Require(f.Locations.SequenceEqual(new[] { path })); return; }
        await library.AddVirtualFolder(Name(uid), CollectionTypeOptions.homevideos, new LibraryOptions
        {
            PathInfos = [new MediaPathInfo { Path = path }],
            EnableRealtimeMonitor = false,
            EnableChapterImageExtraction = false,
            EnableTrickplayImageExtraction = false,
            SaveLocalMetadata = false,
            DisabledMediaSegmentProviders = ["Cleanyfin"],
            TypeOptions = [new TypeOptions{Type="Video",MetadataFetchers=["Cleanyfin shelf facts"],ImageFetchers=[],ImageFetcherOrder=[],
                ImageOptions=[new ImageOption{Type=ImageType.Primary,Limit=0}]}]
        }, false);
    }
    internal async Task<int> Reconcile(bool cleanup, CancellationToken ct)
    {
        await _writer.WaitAsync(ct);
        try
        {
            if (!cleanup && Plugin.Instance?.Configuration is not { EnableAuthRangeLab: true, EnableLegacyRangeFixture: false }) return 0;
            if (!Directory.Exists(Root) && cleanup) return 0;
            GuardLocations(); var rows = Rows(); var current = new HashSet<string>(); var touched = new HashSet<string>();
            if (!cleanup)
            {
                var profiles = Path.Combine(Root, "profiles"); var snapshots = Path.Combine(Root, "snapshots"); NoLinks(profiles); NoLinks(snapshots);
                Directory.CreateDirectory(profiles); Directory.CreateDirectory(snapshots);
                var viewers = Directory.EnumerateDirectories(profiles).Select(Path.GetFileName).Where(GrantTable<Binding>.IsHandle).Take(Limit + 1).ToArray();
                var items = Directory.EnumerateFiles(snapshots, "*.json").Select(Path.GetFileNameWithoutExtension).Where(GrantTable<Binding>.IsHandle).Take(Limit + 1).ToArray();
                Require(viewers.Length <= Limit && items.Length <= Limit);
                foreach (var uid in viewers)
                {
                    if (users.GetUserById(Guid.ParseExact(uid!, "N")) == null) continue;
                    foreach (var id in items)
                    {
                        ct.ThrowIfCancellationRequested(); ShelfInput input;
                        try { input = Resolve(uid!, id!); } catch (Exception ex) when (ex is Refused or IOException or UnauthorizedAccessException) { log.LogDebug("Shelf pair refused: {Reason}", ex is Refused r ? r.Reason : RefusalCode.AbsentSnapshot); continue; }
                        var plan = input.Resolution; var origin = FixtureGate.Origin(FixtureGate.Config);
                        var row = ShelfRow.Reusable(rows, uid!, id!, plan.TimelineKey, origin);
                        if (row == null)
                        {
                            NoLinks(Shelf(uid!)); Directory.CreateDirectory(Shelf(uid!)); var key = Guid.NewGuid().ToString("N"); row = new(key, uid!, id!, input.Source.Id, plan.Plan.Authority.ProfileRef, plan.TimelineKey, input.Generation,
                                checked(plan.Plan.Timeline.FilteredDurationMs * 10000), ShelfRow.InertLocator(origin, key));
                            // Publish private mapping first. A crash can leave only a mapping or a dead pointer, never media.
                            Require(rows.Count < 2 * Limit * Limit); rows.Add(row); Save(rows); Atomic(Pointer(row), Encoding.UTF8.GetBytes(row.Locator + "\n"), false); touched.Add(uid!);
                        }
                        else if (!File.Exists(Pointer(row))) { Atomic(Pointer(row), Encoding.UTF8.GetBytes(row.Locator + "\n"), false); touched.Add(uid!); }
                        if (!Owned(row)) { continue; }
                        await EnsureShelf(uid!);
                        if (row.Generation != input.Generation)
                        {
                            grants.Table.Revoke(b => b.Shelf?.Key == row.Key); var index = rows.IndexOf(row); rows[index] = row = row with { Generation = input.Generation }; Save(rows);
                        }
                        if (library.FindByPath(Pointer(row), false) is not Video indexed || indexed.RunTimeTicks != row.Ticks) touched.Add(uid!);
                        current.Add(row.Key);
                    }
                }
            }
            foreach (var row in rows.ToArray()) if (!current.Contains(row.Key))
                {
                    grants.Table.Revoke(b => b.Shelf?.Key == row.Key); if (Owned(row)) File.Delete(Pointer(row)); rows.Remove(row); Save(rows); touched.Add(row.Viewer);
                }
            // Scan only this profile's native collection. Never schedule a whole-library refresh.
            foreach (var uid in touched)
            {
                var f = library.GetVirtualFolders().SingleOrDefault(f => f.Name == Name(uid) && f.Locations.SequenceEqual(new[] { Shelf(uid) }));
                if (f != null && Guid.TryParse(f.ItemId, out var id) && library.GetItemById(id) is Folder folder)
                    await providers.RefreshFullItem(folder, new MetadataRefreshOptions(new DirectoryService(fileSystem)) { MetadataRefreshMode = MetadataRefreshMode.FullRefresh, ImageRefreshMode = MetadataRefreshMode.None }, ct);
            }
            if (cleanup && rows.Count == 0 && File.Exists(Maps)) File.Delete(Maps);
            return rows.Count;
        }
        finally { _writer.Release(); }
    }
}

internal sealed class ShelfWorker(IServiceProvider services, ILogger<ShelfWorker> log) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            await Task.Delay(TimeSpan.FromSeconds(4), ct);
            if (Plugin.Instance?.Configuration is not { EnableAuthRangeLab: true, EnableLegacyRangeFixture: false }) continue;
            try { await services.GetRequiredService<ShelfStore>().Reconcile(false, ct); } catch (Exception) when (!ct.IsCancellationRequested) { log.LogWarning("Playback shelf reconcile refused"); }
        }
    }
}
