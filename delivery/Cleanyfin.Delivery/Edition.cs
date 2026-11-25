using System.Collections.Immutable;
using System.Diagnostics;
using System.Globalization;
using System.Numerics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;
using static Cleanyfin.Delivery.PlanValidator;

namespace Cleanyfin.Delivery;

// Edition binding: what proves that the bytes a viewer plays are the bytes a reviewer reviewed, on the same
// timeline. Design: knowledge-base/01-working/marking-foundation-2026-11-24/02-household-file-binding.md.
//
//   identity  = full SHA-256 of the file's bytes + the stream layout probed from those same bytes
//   validity  = only while the cheap change signals (inode, size, mtime, ctime) are exactly as they were when
//               the hash was taken; any difference means "unknown until re-hashed", never "probably the same".

/// <summary>A refusal from edition verification. Carries a stable delivery reason, never a path or file content.</summary>
public sealed class EditionRefused(RefusalCode reason) : Exception(reason.ToString())
{
    public RefusalCode Reason { get; } = reason;
}

/// <summary>
/// Cheap change signals for one regular file, read with <c>statx</c> from an open handle. The inode change time
/// (ctime) is set by the kernel on every write, truncate, rename, link or metadata change, and ordinary programs
/// cannot set it back; that is what makes "mtime preserved" replacements visible. Device and mount ids are left
/// out on purpose: they are reassigned on remount and container restart, and would force re-hashing every large
/// file after each reboot without adding a change signal ctime does not already give.
/// </summary>
public sealed record FileStamp(ulong Inode, long Size, long ModifiedSeconds, uint ModifiedNanoseconds,
    long ChangedSeconds, uint ChangedNanoseconds)
{
    [StructLayout(LayoutKind.Explicit, Size = 256)]
    private struct Statx
    {
        [FieldOffset(0)] public uint Mask;
        [FieldOffset(28)] public ushort Mode;
        [FieldOffset(32)] public ulong Inode;
        [FieldOffset(40)] public long Size;
        [FieldOffset(96)] public long ChangeSec;
        [FieldOffset(104)] public uint ChangeNs;
        [FieldOffset(112)] public long ModifySec;
        [FieldOffset(120)] public uint ModifyNs;
    }

    [DllImport("libc", SetLastError = true)]
    private static extern int statx(int dirfd, string path, int flags, uint mask, out Statx buffer);

    private const uint Wanted = 0x1 | 0x40 | 0x80 | 0x100 | 0x200; // type, mtime, ctime, ino, size
    private const int EmptyPath = 0x1000;

    /// <summary>Signals of the file behind an open handle (the bytes that handle reads).</summary>
    public static FileStamp Read(SafeFileHandle handle)
    {
        if (!OperatingSystem.IsLinux() || handle.IsInvalid)
            throw new EditionRefused(RefusalCode.UnverifiedBinding);
        var added = false;
        try
        {
            handle.DangerousAddRef(ref added);
            if (statx(handle.DangerousGetHandle().ToInt32(), "", EmptyPath, Wanted, out var st) != 0 ||
                (st.Mask & Wanted) != Wanted || (st.Mode & 0xf000) != 0x8000 || st.Size < 0)
                throw new EditionRefused(RefusalCode.UnverifiedBinding);
            return new(st.Inode, st.Size, st.ModifySec, st.ModifyNs, st.ChangeSec, st.ChangeNs);
        }
        finally
        {
            if (added)
                handle.DangerousRelease();
        }
    }

    /// <summary>Signals of whatever file the path names right now (follows a symlink to its target).</summary>
    public static FileStamp Read(string path)
    {
        try
        {
            using var handle = File.OpenHandle(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            return Read(handle);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            throw new EditionRefused(RefusalCode.UnverifiedBinding);
        }
    }

    public override string ToString() => FormattableString.Invariant(
        $"{Inode}:{Size}:{ModifiedSeconds}.{ModifiedNanoseconds:D9}:{ChangedSeconds}.{ChangedNanoseconds:D9}");
}

/// <summary>One stream as the probe reported it.</summary>
public sealed record LayoutStream(int Index, string Kind, string Codec, long TimeBaseNumerator,
    long TimeBaseDenominator, long? StartPts);

/// <summary>
/// The container and stream layout probed from the file's own bytes: format, start, duration and every stream's
/// codec, time base and first timestamp. Two files with equal bytes have equal layouts under the same probe; a
/// probe upgrade that reads the same bytes differently is a timeline change and needs review again.
/// </summary>
public sealed record EditionLayout(string Format, long StartMicroseconds, long DurationMicroseconds,
    ImmutableArray<LayoutStream> Streams)
{
    public const int MaxStreams = 256;
    public const int MaxProbeBytes = 256 * 1024;

    /// <summary>Arguments for the server's ffprobe: local file protocol only, no output, JSON on stdout.</summary>
    public static IReadOnlyList<string> ProbeArguments(string path) =>
    [
        "-v", "quiet", "-protocol_whitelist", "file", "-show_entries",
        "format=format_name,start_time,duration:stream=index,codec_type,codec_name,time_base,start_pts",
        "-of", "json", "file:" + path,
    ];

    /// <summary>Whole milliseconds that cover the whole file (rounded up, so a cut to the end leaves nothing).</summary>
    public long DurationMs => (DurationMicroseconds + 999) / 1000;

    /// <summary>The stable text the layout digest is computed from.</summary>
    public string Canonical()
    {
        var b = new StringBuilder("edition-layout-v1\n");
        b.Append(Format).Append('\n').Append(StartMicroseconds.ToString(CultureInfo.InvariantCulture)).Append('\n')
            .Append(DurationMicroseconds.ToString(CultureInfo.InvariantCulture)).Append('\n');
        foreach (var s in Streams)
            b.Append(FormattableString.Invariant(
                $"{s.Index} {s.Kind} {s.Codec} {s.TimeBaseNumerator}/{s.TimeBaseDenominator} {(s.StartPts is { } p ? p.ToString(CultureInfo.InvariantCulture) : "-")}\n"));
        return b.ToString();
    }

    public string Digest() => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(Canonical())));

    /// <summary>
    /// The binding's presentation clock. Source time zero is the earliest first timestamp among the audio and video
    /// streams (what ffmpeg reports as the container start and what mpv rebases to). Each audio/video stream is
    /// anchored at its own first timestamp, which must sit a whole number of milliseconds after zero and must agree
    /// with the probe's own start time; otherwise the clock cannot be written down exactly and the file is refused.
    /// Subtitle, data and attachment streams are part of the layout digest, not of the clock.
    /// </summary>
    public ImmutableArray<TrackClock> Clocks()
    {
        var av = Streams.Where(s => s.Kind is "video" or "audio").ToArray();
        Require(av.Length > 0 && av.Length <= MaxStreams && DurationMicroseconds > 0, RefusalCode.UnsupportedSourceClock);
        Require(av.All(s => s.StartPts.HasValue && s.TimeBaseNumerator > 0 && s.TimeBaseDenominator > 0),
            RefusalCode.UnsupportedSourceClock);
        // Exact rationals: start seconds = pts * num / den.
        static (BigInteger N, BigInteger D) Start(LayoutStream s) => ((BigInteger)s.StartPts!.Value * s.TimeBaseNumerator, s.TimeBaseDenominator);
        var zero = av.Select(Start).Aggregate((a, b) => a.N * b.D <= b.N * a.D ? a : b);
        // The probe prints its start in microseconds (rounded); it must agree with the exact zero to that precision.
        var zeroUs = BigInteger.Divide(zero.N * 1_000_000 + (zero.N.Sign < 0 ? -(zero.D / 2) : zero.D / 2), zero.D);
        Require(BigInteger.Abs(zeroUs - StartMicroseconds) <= 1, RefusalCode.UnsupportedSourceClock);
        var clocks = ImmutableArray.CreateBuilder<TrackClock>(av.Length);
        foreach (var s in av)
        {
            var (n, d) = Start(s);
            // (n/d - zero.N/zero.D) * 1000 must be a whole number of milliseconds.
            var num = (n * zero.D - zero.N * d) * 1000;
            var den = d * zero.D;
            Require(num.Sign >= 0 && BigInteger.Remainder(num, den).IsZero, RefusalCode.UnsupportedSourceClock);
            var ms = (long)(num / den);
            Require(ms < DurationMs, RefusalCode.UnsupportedSourceClock);
            clocks.Add(new TrackClock(s.Index, s.Kind, s.TimeBaseNumerator, s.TimeBaseDenominator, s.StartPts!.Value, ms));
        }
        return clocks.MoveToImmutable();
    }

    /// <summary>Strictly reads ffprobe's JSON for the entries in <see cref="ProbeArguments"/> (empty program/group and chapter sections are ignored).</summary>
    public static EditionLayout ParseFfprobe(ReadOnlySpan<byte> json)
    {
        Require(json.Length is > 0 and <= MaxProbeBytes, RefusalCode.UnverifiedBinding);
        try
        {
            using var doc = JsonDocument.Parse(json.ToArray(), new JsonDocumentOptions { MaxDepth = 8 });
            var root = ReadObject(doc.RootElement, "streams", "format", "?chapters", "?programs", "?stream_groups");
            var format = ReadObject(root["format"], "format_name", "duration", "start_time");
            var streams = root["streams"];
            var list = ImmutableArray.CreateBuilder<LayoutStream>(ArrayCount(streams, MaxStreams, min: 1));
            foreach (var e in streams.EnumerateArray())
            {
                var f = ReadObject(e, "index", "codec_type", "?codec_name", "time_base", "?start_pts", "?side_data_list");
                // ffprobe lists a stream's side data (e.g. E-AC-3 in MP4) as empty objects when no side data entries were
                // requested. Empty entries carry nothing; any content means a format this reader does not know: refuse.
                if (f.TryGetValue("side_data_list", out var side))
                {
                    ArrayCount(side, 16);
                    Require(side.EnumerateArray().All(x => x.ValueKind == JsonValueKind.Object && !x.EnumerateObject().Any()),
                        RefusalCode.UnverifiedBinding);
                }
                var tb = Text(f["time_base"]).Split('/');
                long num = 0, den = 0;
                Require(tb.Length == 2 && long.TryParse(tb[0], NumberStyles.None, CultureInfo.InvariantCulture, out num) &&
                    long.TryParse(tb[1], NumberStyles.None, CultureInfo.InvariantCulture, out den) && num > 0 && den > 0,
                    RefusalCode.UnverifiedBinding);
                var index = Integer(f["index"], 0);
                Require(index == list.Count, RefusalCode.UnverifiedBinding);
                list.Add(new LayoutStream((int)index, Name(f["codec_type"]),
                    f.TryGetValue("codec_name", out var codec) ? Name(codec) : "none", num, den,
                    f.TryGetValue("start_pts", out var pts) ? Integer(pts) : null));
            }
            return new EditionLayout(Name(format["format_name"]), Microseconds(format["start_time"], signed: true),
                Microseconds(format["duration"], signed: false), list.MoveToImmutable());
        }
        catch (Rejected) { throw new EditionRefused(RefusalCode.UnverifiedBinding); }
        catch (Exception e) when (e is JsonException or FormatException or OverflowException or InvalidOperationException)
        {
            throw new EditionRefused(RefusalCode.UnverifiedBinding);
        }
    }

    private static string Name(JsonElement value)
    {
        var text = Text(value, 128);
        Require(text.All(c => char.IsAsciiLetterOrDigit(c) || c is '_' or ',' or '-' or '.'), RefusalCode.UnverifiedBinding);
        return text;
    }

    // ffprobe prints seconds with six decimals ("20.000000", "-0.021333").
    private static long Microseconds(JsonElement value, bool signed)
    {
        var text = Text(value, 32);
        Require(decimal.TryParse(text, signed ? NumberStyles.AllowLeadingSign | NumberStyles.AllowDecimalPoint : NumberStyles.AllowDecimalPoint,
            CultureInfo.InvariantCulture, out var seconds), RefusalCode.UnverifiedBinding);
        var us = seconds * 1_000_000m;
        Require(us == decimal.Truncate(us) && Math.Abs(us) <= MaxJsonInteger, RefusalCode.UnverifiedBinding);
        return (long)us;
    }

    internal static void Require(bool ok, RefusalCode reason)
    {
        if (!ok)
            throw new EditionRefused(reason);
    }
}

/// <summary>
/// One verified edition of one library item: the change signals it was hashed under, the full SHA-256 and the
/// layout. Persisted privately by the household server (<c>edition-record-v1</c>) so a 40 GB file is hashed once,
/// not after every restart. Holds a path because it is local state; plans and snapshots never carry it.
/// </summary>
public sealed record EditionRecord(string ItemId, string MediaSourceId, string Path, FileStamp Stamp, string Sha256,
    EditionLayout Layout, DateTimeOffset VerifiedAt)
{
    public const int Version = 1;
    public const int MaxBytes = 512 * 1024;

    public string LayoutSha256 => Layout.Digest();

    /// <summary>The edition digest: bytes and timeline together. Changes if either changes.</summary>
    public string EditionSha256 => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(
        "edition-v1|" + Sha256 + "|" + LayoutSha256)));

    /// <summary>The cheap check, made on every use: same item, same path, exactly the same change signals.</summary>
    public bool IsCurrent(string itemId, string mediaSourceId, string path, FileStamp now) =>
        ItemId == itemId && MediaSourceId == mediaSourceId && Path == path && Stamp == now;

    /// <summary>The snapshot-v1 binding for this edition. <c>bindingRev</c> names the edition digest.</summary>
    public SourceBinding ToBinding(string serverRef) => new(serverRef, ItemId, MediaSourceId, Sha256, Stamp.Size,
        "clock:" + ItemId + ":" + LayoutSha256, "bind:" + ItemId + ":" + EditionSha256, Layout.DurationMs,
        SourceOrigin, Layout.Clocks());

    public VerifiedSourceIdentity Identity => new(ItemId, MediaSourceId, Sha256, Stamp.Size, Layout.DurationMs);

    /// <summary>How a new verification of the same item relates to this one.</summary>
    public EditionChange Compare(EditionRecord next) =>
        Sha256 != next.Sha256 ? EditionChange.Content
        : LayoutSha256 != next.LayoutSha256 ? EditionChange.Timeline
        : EditionChange.None;

    public byte[] Write()
    {
        using var m = new MemoryStream();
        using (var w = new Utf8JsonWriter(m))
        {
            w.WriteStartObject();
            w.WriteNumber("editionVersion", Version);
            w.WriteString("itemId", ItemId);
            w.WriteString("mediaSourceId", MediaSourceId);
            w.WriteString("path", Path);
            w.WriteStartObject("stamp");
            w.WriteString("inode", Stamp.Inode.ToString(CultureInfo.InvariantCulture));
            w.WriteNumber("size", Stamp.Size);
            w.WriteNumber("mtimeSec", Stamp.ModifiedSeconds);
            w.WriteNumber("mtimeNsec", Stamp.ModifiedNanoseconds);
            w.WriteNumber("ctimeSec", Stamp.ChangedSeconds);
            w.WriteNumber("ctimeNsec", Stamp.ChangedNanoseconds);
            w.WriteEndObject();
            w.WriteString("sha256", Sha256);
            w.WriteStartObject("layout");
            w.WriteString("format", Layout.Format);
            w.WriteNumber("startUs", Layout.StartMicroseconds);
            w.WriteNumber("durationUs", Layout.DurationMicroseconds);
            w.WriteStartArray("streams");
            foreach (var s in Layout.Streams)
            {
                w.WriteStartObject();
                w.WriteNumber("index", s.Index);
                w.WriteString("kind", s.Kind);
                w.WriteString("codec", s.Codec);
                w.WriteStartArray("timeBase");
                w.WriteNumberValue(s.TimeBaseNumerator);
                w.WriteNumberValue(s.TimeBaseDenominator);
                w.WriteEndArray();
                if (s.StartPts is { } p)
                    w.WriteNumber("startPts", p);
                w.WriteEndObject();
            }
            w.WriteEndArray();
            w.WriteEndObject();
            w.WriteString("layoutSha256", LayoutSha256);
            w.WriteString("verifiedAt", VerifiedAt.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture));
            w.WriteEndObject();
        }
        return m.ToArray();
    }

    public static Decision<EditionRecord> Parse(ReadOnlyMemory<byte> utf8)
    {
        if (utf8.IsEmpty)
            return Decision<EditionRecord>.Refuse(RefusalCode.UnverifiedBinding);
        if (utf8.Length > MaxBytes)
            return Decision<EditionRecord>.Refuse(RefusalCode.LimitExceeded);
        try
        {
            using var doc = ReadDocument(utf8);
            var f = ReadObject(doc.RootElement, "editionVersion", "itemId", "mediaSourceId", "path", "stamp", "sha256",
                "layout", "layoutSha256", "verifiedAt");
            Check(Integer(f["editionVersion"]) == Version, RefusalCode.UnsupportedVersion);
            var st = ReadObject(f["stamp"], "inode", "size", "mtimeSec", "mtimeNsec", "ctimeSec", "ctimeNsec");
            Check(ulong.TryParse(Text(st["inode"], 20), NumberStyles.None, CultureInfo.InvariantCulture, out var inode),
                RefusalCode.InvalidValue);
            var stamp = new FileStamp(inode, Integer(st["size"], 0), Integer(st["mtimeSec"]),
                checked((uint)Integer(st["mtimeNsec"], 0)), Integer(st["ctimeSec"]), checked((uint)Integer(st["ctimeNsec"], 0)));
            var l = ReadObject(f["layout"], "format", "startUs", "durationUs", "streams");
            var items = l["streams"];
            var streams = ImmutableArray.CreateBuilder<LayoutStream>(ArrayCount(items, EditionLayout.MaxStreams, min: 1));
            foreach (var e in items.EnumerateArray())
            {
                var s = ReadObject(e, "index", "kind", "codec", "timeBase", "?startPts");
                var tb = s["timeBase"];
                Check(tb.ValueKind == JsonValueKind.Array && tb.GetArrayLength() == 2, RefusalCode.InvalidValue);
                var index = Integer(s["index"], 0);
                Check(index == streams.Count, RefusalCode.InvalidValue);
                streams.Add(new LayoutStream((int)index, Text(s["kind"], 64), Text(s["codec"], 128), Integer(tb[0], 1),
                    Integer(tb[1], 1), s.TryGetValue("startPts", out var p) ? Integer(p) : null));
            }
            var layout = new EditionLayout(Text(l["format"], 128), Integer(l["startUs"]), Integer(l["durationUs"], 1),
                streams.MoveToImmutable());
            var path = Text(f["path"], 4096);
            Check(System.IO.Path.IsPathFullyQualified(path) && !path.Any(char.IsControl), RefusalCode.InvalidValue);
            var record = new EditionRecord(Reference(f["itemId"]), Reference(f["mediaSourceId"]), path, stamp,
                PlanValidator.Digest(f["sha256"]), layout, Timestamp(f["verifiedAt"]));
            Check(stamp.Size > 0, RefusalCode.InvalidValue);
            // The stored layout digest must be the one this reader computes: a hand edit cannot move the clock.
            Check(PlanValidator.Digest(f["layoutSha256"]) == record.LayoutSha256, RefusalCode.InvalidDigest);
            return Decision<EditionRecord>.Allow(record);
        }
        catch (Rejected e) { return Decision<EditionRecord>.Refuse(e.Reason); }
        catch (Exception e) when (e is JsonException or DecoderFallbackException)
        {
            return Decision<EditionRecord>.Refuse(RefusalCode.InvalidJson);
        }
        catch (OverflowException) { return Decision<EditionRecord>.Refuse(RefusalCode.IntegerOverflow); }
    }
}

public enum EditionChange
{
    None = 0,
    /// <summary>The bytes differ: a replaced, edited or re-encoded file. Marks need review again.</summary>
    Content = 1,
    /// <summary>Same bytes, read as a different timeline (a probe change). Marks need review again.</summary>
    Timeline = 2,
}

/// <summary>Result of one full verification: the signals it held under, the full SHA-256 and the layout.</summary>
public sealed record EditionHash(FileStamp Stamp, string Sha256, EditionLayout Layout, TimeSpan Elapsed);

/// <summary>
/// Hashes a whole file once, in the background, while proving it did not change underneath: the change signals of
/// the open handle and of the path are taken before the probe and again after the last byte, and must all agree.
/// Returns null (not a refusal) when the file changed while being read; the caller simply tries again later.
/// </summary>
public static class EditionHasher
{
    public const int ChunkBytes = 256 * 1024;

    public static EditionHash? Verify(string path, Func<string, EditionLayout> probe, long bytesPerSecond,
        Action<long, long>? progress, CancellationToken cancel, Action<TimeSpan>? sleep = null)
    {
        sleep ??= d => cancel.WaitHandle.WaitOne(d);
        var clock = Stopwatch.StartNew();
        FileStream file;
        try
        {
            file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete,
                bufferSize: 0, FileOptions.SequentialScan);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            throw new EditionRefused(RefusalCode.UnverifiedBinding);
        }
        using (file)
        {
            var before = FileStamp.Read(file.SafeFileHandle);
            if (FileStamp.Read(path) != before)
                return null;
            var layout = probe(path);
            using var sha = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            var buffer = new byte[ChunkBytes];
            long done = 0;
            progress?.Invoke(0, before.Size);
            int read;
            while ((read = file.Read(buffer, 0, buffer.Length)) > 0)
            {
                cancel.ThrowIfCancellationRequested();
                sha.AppendData(buffer, 0, read);
                done += read;
                if (done > before.Size)
                    return null;
                progress?.Invoke(done, before.Size);
                if (bytesPerSecond > 0)
                {
                    var due = TimeSpan.FromSeconds((double)done / bytesPerSecond) - clock.Elapsed;
                    if (due > TimeSpan.Zero)
                        sleep(due);
                }
            }
            if (done != before.Size || FileStamp.Read(file.SafeFileHandle) != before || FileStamp.Read(path) != before)
                return null;
            return new EditionHash(before, Convert.ToHexStringLower(sha.GetHashAndReset()), layout, clock.Elapsed);
        }
    }
}
