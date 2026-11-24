using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Linq;
using System.Security.Cryptography;
using Cleanyfin.Delivery;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.MediaInfo;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

// The lab's edition verifier: one implementation of IEditionVerifier, with the same record, digest and binding as
// household files, plus lab-only restrictions. Only operator-controlled lab inodes mounted individually, read-only,
// with no concurrent host writer. Small enough (64 MiB) to hash in the request; the exact fixture layout only.
// A read-only directory or matching path, mtime, runtime and length is NOT an immutable asset.
internal sealed class PinnedAsset : IEditionVerifier
{
    private const string Directory = "/fixture-library";
    private readonly Dictionary<string, EditionRecord> _cache = new();

    internal static bool Claims(Video video) => Path.GetDirectoryName(video.Path) == Directory;

    // Change signals of a small operator document (profile, snapshot, device), for consistent-read checks.
    internal static string FileVersion(string path)
    {
        using var f = File.OpenRead(path);
        try { return FileStamp.Read(f.SafeFileHandle).ToString(); }
        catch (EditionRefused e) { throw new Refused(e.Reason); }
    }

    public EditionStatus Status(Video video)
    {
        Verify(video);
        return new("verified", 0, 0, null);
    }

    public VerifiedEdition Verify(Video video)
    {
        try { return VerifyPinned(video); }
        catch (EditionRefused e) { throw new Refused(e.Reason); }
    }

    private VerifiedEdition VerifyPinned(Video video)
    {
        var path = video.Path;
        FixtureGate.Require(OperatingSystem.IsLinux() && Claims(video) && Path.GetExtension(path) == ".mp4" &&
            !video.IsShortcut && video.SubtitleFiles.Length == 0 && video.AudioFiles.Length == 0 && !video.HasLocalAlternateVersions && video.LinkedAlternateVersions.Length == 0 &&
            File.ReadLines("/proc/self/mountinfo").Any(l => l.Split(' ') is [_, _, _, _, var mount, var opts, ..] && mount == path && opts.Split(',').Contains("ro")), RefusalCode.UnverifiedBinding);
        var sources = video.GetMediaSources(false);
        var src = sources.Count == 1 ? sources[0] : null;
        var id = video.Id.ToString("N");
        FixtureGate.Require(src != null && src.Path == path && src.Id == id && Matches(src), RefusalCode.BindingMismatch);
        using var f = File.OpenRead(path);
        var stamp = FileStamp.Read(f.SafeFileHandle);
        FixtureGate.Require(stamp.Size == src!.Size && stamp.Size is > 0 and <= 67108864, RefusalCode.BindingMismatch);
        lock (_cache)
        {
            if (!_cache.TryGetValue(path, out var record) || !record.IsCurrent(id, src.Id, path, stamp))
            {
                var layout = CheckPhysicalTracks(path, src);
                var digest = Convert.ToHexStringLower(SHA256.HashData(f));
                FixtureGate.Require(FileStamp.Read(f.SafeFileHandle) == stamp, RefusalCode.BindingMismatch);
                if (_cache.Count >= 64) _cache.Clear();
                _cache[path] = record = new EditionRecord(id, src.Id, path, stamp, digest, layout, DateTimeOffset.UtcNow);
            }
            FixtureGate.Require(record.Layout.DurationMs * 10000 == src.RunTimeTicks, RefusalCode.BindingMismatch);
            return new(src, record, "lab:" + stamp);
        }
    }

    // Catalogue duration may still describe the old inode. Probe actual container metadata once per version.
    // The pinned image owns this ffprobe; no shell, remote protocols, sidecars or media output.
    private static EditionLayout CheckPhysicalTracks(string path, MediaSourceInfo src)
    {
        string[] args = ["-v", "quiet", "-protocol_whitelist", "file", "-enable_drefs", "0", "-f", "mov", "-show_entries",
            "format=format_name,start_time,duration:stream=index,codec_type,codec_name,time_base,start_pts:chapter=id", "-of", "json", path];
        var text = Ffprobe.Run("/usr/lib/jellyfin-ffmpeg/ffprobe", args, TimeSpan.FromSeconds(5), 8192);
        try
        {
            using var json = JsonDocument.Parse(text);
            FixtureGate.Require(json.RootElement.GetProperty("chapters").GetArrayLength() == 0, RefusalCode.UnverifiedBinding);
        }
        catch (Exception ex) when (ex is JsonException or InvalidOperationException or KeyNotFoundException) { throw new Refused(RefusalCode.UnverifiedBinding); }
        var layout = EditionLayout.ParseFfprobe(text);
        FixtureGate.Require(layout.Streams.Length == 2 && layout.StartMicroseconds == 0 && layout.DurationMicroseconds * 10 == src.RunTimeTicks, RefusalCode.UnverifiedBinding);
        for (int i = 0; i < 2; i++)
        {
            var t = layout.Streams[i];
            FixtureGate.Require(t.Index == i && t.StartPts == 0 && t.Codec == (i == 0 ? "h264" : "aac") &&
                (t.TimeBaseNumerator, t.TimeBaseDenominator) == (i == 0 ? (1, 12800) : (1, 48000)), RefusalCode.UnverifiedBinding);
        }
        return layout;
    }

    internal static bool Matches(MediaSourceInfo src) => src.Protocol == MediaProtocol.File && src.VideoType == VideoType.VideoFile &&
        !src.IsRemote && !src.IsInfiniteStream && !src.RequiresOpening && src.SupportsDirectPlay && PinnedMp4Container.DescribesSource(src.Container) &&
        src.RunTimeTicks is > 0 && src.RunTimeTicks % 10000 == 0 && src.MediaAttachments.Count == 0 && src.MediaStreams.Count == 2 &&
        src.MediaStreams.All(s => !s.IsExternal && s.IsExternalUrl != true) &&
        src.MediaStreams.Any(s => s.Index == 0 && s.Type == MediaStreamType.Video && s.Codec == "h264") &&
        src.MediaStreams.Any(s => s.Index == 1 && s.Type == MediaStreamType.Audio && s.Codec == "aac");
}
