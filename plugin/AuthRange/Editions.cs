using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Cleanyfin.Delivery;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.MediaInfo;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

/// <summary>
/// The exact file an item plays, proven: the full SHA-256 and stream layout of its bytes, valid only while the file's
/// cheap change signals are exactly those it was hashed under. Re-checked on every use; never blocks on hashing.
/// </summary>
internal sealed record VerifiedEdition(MediaSourceInfo Source, EditionRecord Record, string Version)
{
    internal VerifiedSourceIdentity Identity => Record.Identity;
    internal SourceBinding Binding(string serverRef) => Record.ToBinding(serverRef);
}

/// <summary>What the review page shows about an item's file.</summary>
internal sealed record EditionStatus(string State, long Done, long Total, string? Reason);

/// <summary>One way to prove an item's edition. The lab's pinned fixture and ordinary household files are two of them.</summary>
internal interface IEditionVerifier
{
    /// <summary>The verified edition, or <see cref="Refused"/> (never a guess, never a wait for a hash).</summary>
    VerifiedEdition Verify(Video video);

    EditionStatus Status(Video video);
}

/// <summary>Routes an item to its verifier: the operator-pinned lab fixture, or an ordinary household file.</summary>
internal sealed class Editions(PinnedAsset lab, HouseholdEditions household)
{
    private IEditionVerifier For(Video video) => PinnedAsset.Claims(video) ? lab : household;

    internal VerifiedEdition Verify(Video video)
    {
        try { return For(video).Verify(video); }
        catch (EditionRefused e) { throw new Refused(e.Reason); }
    }

    internal EditionStatus Status(Video video)
    {
        try { return For(video).Status(video); }
        catch (EditionRefused e) { return new("refused", 0, 0, e.Reason.ToString()); }
        catch (Refused r) { return new("refused", 0, 0, r.Reason.ToString()); }
    }
}

/// <summary>Runs ffprobe with no shell, a bounded time and bounded output, reading stdout while it runs.</summary>
internal static class Ffprobe
{
    internal static byte[] Run(string program, IEnumerable<string> args, TimeSpan timeout, int maxBytes)
    {
        var info = new ProcessStartInfo(program) { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = false };
        foreach (var a in args)
            info.ArgumentList.Add(a);
        using var child = Process.Start(info) ?? throw new EditionRefused(RefusalCode.UnverifiedBinding);
        try
        {
            using var output = new MemoryStream();
            var copy = Task.Run(() =>
            {
                var buffer = new byte[16384];
                int n;
                while ((n = child.StandardOutput.BaseStream.Read(buffer, 0, buffer.Length)) > 0)
                {
                    if (output.Length + n > maxBytes)
                        return false;
                    output.Write(buffer, 0, n);
                }
                return true;
            });
            if (!child.WaitForExit(timeout) || !copy.Wait(timeout) || !copy.Result || child.ExitCode != 0)
                throw new EditionRefused(RefusalCode.UnverifiedBinding);
            return output.ToArray();
        }
        finally
        {
            if (!child.HasExited)
            {
                child.Kill(true);
                child.WaitForExit();
            }
        }
    }
}

/// <summary>
/// Ordinary household library files. The first use of an item queues one background job that probes the layout and
/// hashes the whole file (one file at a time, optionally paced by <c>EditionHashBytesPerSecond</c> so a NAS is not
/// saturated) and stores an <c>edition-record-v1</c> under the plugin's configuration folder. Until that record exists
/// and matches the file's current change signals, every use refuses with <see cref="RefusalCode.VerificationPending"/>.
/// A changed signal (replace, edit, touch, rename, new hard link) makes the record not current: a new hash is queued,
/// and only if bytes and layout come out identical does the same binding come back.
/// </summary>
internal sealed class HouseholdEditions(IMediaEncoder encoder, ILogger<HouseholdEditions> log) : IEditionVerifier, IDisposable
{
    internal const string Folder = "cleanyfin-editions-v1";
    private const int MaxQueued = 256;

    private sealed class Job(string item, string source, string path, FileStamp stamp)
    {
        internal string Item { get; } = item;
        internal string Source { get; } = source;
        internal string Path { get; } = path;
        internal FileStamp Stamp { get; } = stamp;
        internal long Done;
        internal long Total = stamp.Size;
        internal bool Running;
    }

    private readonly object _gate = new();
    private readonly Dictionary<string, EditionRecord?> _records = new(StringComparer.Ordinal);
    private readonly Dictionary<string, Job> _jobs = new(StringComparer.Ordinal);
    private readonly Dictionary<string, (FileStamp Stamp, RefusalCode Reason)> _failed = new(StringComparer.Ordinal);
    private readonly Queue<Job> _queue = new();
    private readonly CancellationTokenSource _stop = new();
    private Task? _worker;

    private static string Root => System.IO.Path.Combine(Plugin.Instance!.ConfigurationsPath, Folder);

    public VerifiedEdition Verify(Video video)
    {
        var (src, id, path) = Select(video);
        var stamp = FileStamp.Read(path);
        lock (_gate)
        {
            var record = Load(id);
            if (record != null && record.IsCurrent(id, src.Id, path, stamp))
            {
                Consistent(src, record);
                return new(src, record, "household:" + stamp + ":" + record.EditionSha256);
            }
            if (_failed.TryGetValue(id, out var failed) && failed.Stamp == stamp)
                throw new Refused(failed.Reason);
            Enqueue(new Job(id, src.Id, path, stamp));
            throw new Refused(RefusalCode.VerificationPending);
        }
    }

    public EditionStatus Status(Video video)
    {
        try
        {
            Verify(video);
            return new("verified", 0, 0, null);
        }
        catch (Refused r) when (r.Reason == RefusalCode.VerificationPending)
        {
            lock (_gate)
            {
                var id = video.Id.ToString("N");
                var changed = Load(id) != null;
                return _jobs.TryGetValue(id, out var job)
                    ? new(job.Running ? "verifying" : "queued", Interlocked.Read(ref job.Done), job.Total, changed ? "FileSignalsChanged" : null)
                    : new("queued", 0, 0, changed ? "FileSignalsChanged" : null);
            }
        }
        catch (Refused r)
        {
            return new("refused", 0, 0, r.Reason.ToString());
        }
    }

    // An ordinary, local, single-file video of one Jellyfin item. Everything else is not bindable (yet).
    private static (MediaSourceInfo Source, string Id, string Path) Select(Video video)
    {
        var id = video.Id.ToString("N");
        var path = video.Path;
        // One file is the whole title: no stacked parts, alternate versions or external audio that a player could
        // switch to (their bytes are not in the hash).
        FixtureGate.Require(OperatingSystem.IsLinux() && !video.IsShortcut && path is { Length: > 0 } &&
            video.AdditionalParts.Length == 0 && !video.HasLocalAlternateVersions && video.LinkedAlternateVersions.Length == 0 &&
            video.AudioFiles.Length == 0 &&
            System.IO.Path.IsPathFullyQualified(path) && !path.Any(char.IsControl) && Plugin.Instance?.IsShelfPath(path) == false &&
            !path.StartsWith(Plugin.Instance.ConfigurationsPath + "/", StringComparison.Ordinal), RefusalCode.UnverifiedBinding);
        var src = video.GetMediaSources(false).SingleOrDefault(s => s.Id == id);
        FixtureGate.Require(src != null && src.Path == path && src.Protocol == MediaProtocol.File && src.VideoType == VideoType.VideoFile &&
            !src.IsRemote && !src.IsInfiniteStream && !src.RequiresOpening && src.RunTimeTicks is > 0, RefusalCode.UnverifiedBinding);
        return (src!, id, path);
    }

    // Jellyfin's own probe (the catalogue the player is served from) must describe the same file we hashed.
    // A stale catalogue after a replacement refuses until the library is rescanned; it never wins over the bytes.
    private static void Consistent(MediaSourceInfo src, EditionRecord record)
    {
        var mine = record.Layout.Streams.Where(s => s.Kind is "video" or "audio").Select(s => (s.Index, s.Kind)).ToArray();
        var theirs = src.MediaStreams.Where(s => !s.IsExternal && s.Type is MediaStreamType.Video or MediaStreamType.Audio)
            .OrderBy(s => s.Index).Select(s => (s.Index, s.Type == MediaStreamType.Video ? "video" : "audio")).ToArray();
        FixtureGate.Require((src.Size is null || src.Size == record.Stamp.Size) &&
            Math.Abs(src.RunTimeTicks!.Value - record.Layout.DurationMicroseconds * 10) <= 10_000 && mine.SequenceEqual(theirs),
            RefusalCode.BindingMismatch);
    }

    private void Enqueue(Job job)
    {
        if (_jobs.ContainsKey(job.Item) || _queue.Count >= MaxQueued)
            return;
        _jobs[job.Item] = job;
        _queue.Enqueue(job);
        _worker ??= Task.Factory.StartNew(Work, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default);
    }

    private void Work()
    {
        try { Thread.CurrentThread.Priority = ThreadPriority.BelowNormal; } catch (Exception) { /* not permitted: run at normal priority */ }
        while (true)
        {
            Job job;
            lock (_gate)
            {
                if (_queue.Count == 0 || _stop.IsCancellationRequested)
                {
                    _worker = null;
                    return;
                }
                job = _queue.Dequeue();
                job.Running = true;
            }
            var reason = RefusalCode.UnverifiedBinding;
            var failed = false;
            EditionRecord? made = null;
            var stamp = job.Stamp;
            try
            {
                var pace = Math.Max(0, Plugin.Instance?.Configuration.EditionHashBytesPerSecond ?? 0);
                var hash = EditionHasher.Verify(job.Path, Probe, pace, (done, total) =>
                {
                    Interlocked.Exchange(ref job.Done, done);
                    job.Total = total;
                }, _stop.Token);
                if (hash != null)
                {
                    stamp = hash.Stamp;
                    var record = new EditionRecord(job.Item, job.Source, job.Path, hash.Stamp, hash.Sha256, hash.Layout, DateTimeOffset.UtcNow);
                    _ = record.ToBinding("check"); // the clock must be exact, or the edition is refused
                    Save(record);
                    made = record;
                    log.LogInformation("Edition verified: {Bytes} bytes in {Seconds:F3} s ({Rate:F1} MB/s)", hash.Stamp.Size,
                        hash.Elapsed.TotalSeconds, hash.Stamp.Size / 1e6 / Math.Max(hash.Elapsed.TotalSeconds, 1e-6));
                }
                else
                    log.LogInformation("Edition check restarted: the file changed while it was read");
            }
            catch (OperationCanceledException) { return; }
            catch (EditionRefused e) { reason = e.Reason; failed = true; }
            catch (Refused r) { reason = r.Reason; failed = true; }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or InvalidOperationException or System.ComponentModel.Win32Exception)
            { failed = true; }
            lock (_gate)
            {
                _jobs.Remove(job.Item);
                if (made != null)
                {
                    _records[job.Item] = made;
                    _failed.Remove(job.Item);
                }
                // A refusal holds until the file's signals change. A file that changed while it was being read is
                // simply queued again by its next use.
                else if (failed)
                    _failed[job.Item] = (stamp, reason);
            }
        }
    }

    private EditionLayout Probe(string path)
    {
        var program = encoder.ProbePath;
        FixtureGate.Require(!string.IsNullOrEmpty(program) && System.IO.Path.IsPathFullyQualified(program), RefusalCode.UnverifiedBinding);
        return EditionLayout.ParseFfprobe(Ffprobe.Run(program, EditionLayout.ProbeArguments(path), TimeSpan.FromSeconds(60), EditionLayout.MaxProbeBytes));
    }

    private EditionRecord? Load(string id)
    {
        if (_records.TryGetValue(id, out var cached))
            return cached;
        EditionRecord? record = null;
        try
        {
            var path = System.IO.Path.Combine(Root, id + ".json");
            ShelfStore.NoLinks(path);
            if (File.Exists(path))
            {
                using var s = File.OpenRead(path);
                if (s.Length is > 0 and <= EditionRecord.MaxBytes)
                {
                    var b = new byte[s.Length];
                    s.ReadExactly(b);
                    // A record that does not parse or names another item is ignored and re-made by a fresh hash.
                    record = EditionRecord.Parse(b).Value is { } r && r.ItemId == id ? r : null;
                }
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or Refused) { record = null; }
        _records[id] = record;
        return record;
    }

    private static void Save(EditionRecord record)
    {
        FixtureGate.Require(GrantTable<Binding>.IsHandle(record.ItemId), RefusalCode.UnverifiedBinding);
        ShelfStore.NoLinks(Root);
        Directory.CreateDirectory(Root);
        Marking.PluginMarkingStore.Atomic(System.IO.Path.Combine(Root, record.ItemId + ".json"), record.Write());
    }

    public void Dispose()
    {
        _stop.Cancel();
        _stop.Dispose();
    }
}
