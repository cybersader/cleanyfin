using System;
using System.Collections.Generic;
using System.IO;
using System.Diagnostics;
using System.Globalization;
using System.Text.Json;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Cleanyfin.Delivery;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.MediaInfo;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

// Only operator-controlled lab inodes mounted individually, read-only, with no concurrent host writer.
// A read-only directory or matching path, mtime, runtime and length is NOT an immutable asset.
internal sealed class PinnedAsset
{
    [StructLayout(LayoutKind.Explicit, Size = 256)]
    private struct Stat
    {
        [FieldOffset(0)] public uint Mask; [FieldOffset(28)] public ushort Mode;
        [FieldOffset(32)] public ulong Inode; [FieldOffset(40)] public long Size; [FieldOffset(96)] public long ChangeSec;
        [FieldOffset(104)] public uint ChangeNs; [FieldOffset(144)] public ulong Mount;
    }
    [DllImport("libc", SetLastError = true)] private static extern int statx(int fd, string path, int flags, uint mask, out Stat stat);
    private sealed record Stamp(ulong Mount, ulong Inode, long Size, long Sec, uint Ns);
    private readonly Dictionary<string, (Stamp Stamp, string Digest)> _cache = new();
    private static Stamp Identity(FileStream f)
    {
        const uint mask = 0x100 | 0x200 | 0x80 | 0x1000 | 1; // ino, size, ctime, mount-id, type
        FixtureGate.Require(statx(f.SafeFileHandle.DangerousGetHandle().ToInt32(), "", 0x1000, mask, out var st) == 0 &&
            (st.Mask & mask) == mask && (st.Mode & 0xf000) == 0x8000, RefusalCode.UnverifiedBinding);
        return new(st.Mount, st.Inode, st.Size, st.ChangeSec, st.ChangeNs);
    }
    internal static string FileVersion(string path) { using var f = File.OpenRead(path); return Identity(f).ToString(); }
    internal (MediaSourceInfo Source, VerifiedSourceIdentity Identity, string Version) Verify(Video video)
    {
        var path = video.Path;
        FixtureGate.Require(OperatingSystem.IsLinux() && Path.GetDirectoryName(path) == "/fixture-library" && Path.GetExtension(path) == ".mp4" &&
            !video.IsShortcut && video.SubtitleFiles.Length == 0 && video.AudioFiles.Length == 0 && !video.HasLocalAlternateVersions && video.LinkedAlternateVersions.Length == 0 &&
            File.ReadLines("/proc/self/mountinfo").Any(l => l.Split(' ') is [_, _, _, _, var mount, var opts, ..] && mount == path && opts.Split(',').Contains("ro")), RefusalCode.UnverifiedBinding);
        var sources = video.GetMediaSources(false);
        var src = sources.Count == 1 ? sources[0] : null;
        FixtureGate.Require(src != null && src.Path == path && src.Id == video.Id.ToString("N") && Matches(src), RefusalCode.BindingMismatch);
        using var f = File.OpenRead(path);
        var stamp = Identity(f);
        FixtureGate.Require(stamp.Size == src!.Size && stamp.Size is > 0 and <= 67108864, RefusalCode.BindingMismatch);
        lock (_cache)
        {
            if (!_cache.TryGetValue(path, out var entry) || entry.Stamp != stamp)
            {
                CheckPhysicalTracks(path, src);
                var digest = Convert.ToHexStringLower(SHA256.HashData(f));
                FixtureGate.Require(Identity(f) == stamp, RefusalCode.BindingMismatch);
                if (_cache.Count >= 64) _cache.Clear();
                _cache[path] = entry = (stamp, digest);
            }
            return (src, new(video.Id.ToString("N"), src.Id, entry.Digest, stamp.Size, src.RunTimeTicks!.Value / 10000), stamp.ToString());
        }
    }
    // Catalogue duration may still describe the old inode. Probe actual container metadata once per version.
    // The pinned image owns this ffprobe; no shell, remote protocols, sidecars or media output.
    private static void CheckPhysicalTracks(string path, MediaSourceInfo src)
    {
        var info = new ProcessStartInfo("/usr/lib/jellyfin-ffmpeg/ffprobe") { UseShellExecute = false, RedirectStandardOutput = true };
        foreach (var arg in new[]{"-v","quiet","-protocol_whitelist","file","-enable_drefs","0","-f","mov","-show_entries",
            "format=duration:stream=index,codec_type,codec_name,time_base,start_pts:chapter=id","-of","json",path}) info.ArgumentList.Add(arg);
        using var child = Process.Start(info) ?? throw new Refused(RefusalCode.UnverifiedBinding);
        try
        {
            // Bound output by the pipe capacity. If a malformed file fills it, the timeout kills the probe.
            if (!child.WaitForExit(5000)) throw new Refused(RefusalCode.UnverifiedBinding);
            var text = child.StandardOutput.ReadToEnd(); FixtureGate.Require(child.ExitCode == 0 && text.Length is > 0 and < 8192, RefusalCode.UnverifiedBinding);
            using var json = JsonDocument.Parse(text); var r = json.RootElement; var streams = r.GetProperty("streams");
            FixtureGate.Require(streams.GetArrayLength() == 2 && r.GetProperty("chapters").GetArrayLength() == 0 &&
                decimal.TryParse(r.GetProperty("format").GetProperty("duration").GetString(), NumberStyles.AllowDecimalPoint, CultureInfo.InvariantCulture, out var sec) && sec * 10000000 == src.RunTimeTicks,
                RefusalCode.UnverifiedBinding);
            for (int i = 0; i < 2; i++)
            {
                var t = streams[i]; FixtureGate.Require(t.GetProperty("index").GetInt32() == i && t.GetProperty("start_pts").GetInt64() == 0 &&
                t.GetProperty("codec_name").GetString() == (i == 0 ? "h264" : "aac") && t.GetProperty("time_base").GetString() == (i == 0 ? "1/12800" : "1/48000"), RefusalCode.UnverifiedBinding);
            }
        }
        catch (Exception ex) when (ex is JsonException or InvalidOperationException or KeyNotFoundException or FormatException) { throw new Refused(RefusalCode.UnverifiedBinding); }
        finally { if (!child.HasExited) { child.Kill(true); child.WaitForExit(); } }
    }
    internal static bool Matches(MediaSourceInfo src) => src.Protocol == MediaProtocol.File && src.VideoType == VideoType.VideoFile &&
        !src.IsRemote && !src.IsInfiniteStream && !src.RequiresOpening && src.SupportsDirectPlay && src.Container is ("mp4" or "mov,mp4,m4a,3gp,3g2,mj2") &&
        src.RunTimeTicks is > 0 && src.RunTimeTicks % 10000 == 0 && src.MediaAttachments.Count == 0 && src.MediaStreams.Count == 2 &&
        src.MediaStreams.All(s => !s.IsExternal && s.IsExternalUrl != true) &&
        src.MediaStreams.Any(s => s.Index == 0 && s.Type == MediaStreamType.Video && s.Codec == "h264") &&
        src.MediaStreams.Any(s => s.Index == 1 && s.Type == MediaStreamType.Audio && s.Codec == "aac");
}
