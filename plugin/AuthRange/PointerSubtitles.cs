using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Cleanyfin.Delivery;
using MediaBrowser.Controller.MediaEncoding;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

internal sealed record SafeSubtitle(MpvSafeSubtitle Track, byte[] Bytes);
internal sealed record PointerSubtitleSet(MpvAvTrack[] Av, SafeSubtitle[] Safe)
{
    internal MpvSubtitleLayout Layout(UInt128 ticket) => new(Av, Safe.Select(s => s.Track).ToArray(), ticket);

    // These tracks are embedded in the virtual EDL. Never project a native DeliveryUrl or an original subtitle index.
    internal MediaStream[] Project(MediaSourceInfo source) => PointerFixture.Streams(source)
        .Where(s => s.Type != MediaStreamType.Subtitle).OrderBy(s => s.Index)
        .Concat(Safe.Select(x => new MediaStream { Index = x.Track.Index, Type = MediaStreamType.Subtitle,
            Codec = x.Track.Ass ? "ass" : "subrip", IsDefault = x.Track.IsDefault, IsForced = x.Track.IsForced,
            Language = x.Track.Language, Title = x.Track.Title })).ToArray();
}

internal static class PointerSubtitles
{
    private const int MaxSetBytes = 512 * 1024;
    private const int MaxProbeBytes = 4 * 1024 * 1024;
    private static readonly SemaphoreSlim Extractors = new(2);

    internal static async Task<PointerSubtitleSet?> Prepare(Binding binding, MediaSourceInfo source, IMediaEncoder encoder,
        CancellationToken cancellation)
    {
        var tracks = source.MediaStreams.OrderBy(s => s.Index).ToArray();
        var av = tracks.Where(s => s.Type != MediaStreamType.Subtitle).ToArray();
        var subtitles = tracks.Where(s => s.Type == MediaStreamType.Subtitle).ToArray();
        if (subtitles.Length == 0) return null;
        var program = encoder.ProbePath;
        var container = binding.Evidence!.Tuple.Source.Container;
        var partitions = PointerEvidence.Partitions(binding.Evidence);
        FixtureGate.Require(program is { Length: > 0 } && Path.IsPathFullyQualified(program) &&
            Path.IsPathFullyQualified(binding.FilePath) &&
            tracks.SequenceEqual(av.Concat(subtitles)) && tracks.Select((t, i) => t.Index == i).All(x => x) &&
            partitions != MpvPartitions.SplitPictureAndSound, RefusalCode.UnsupportedSubtitle);
        FixtureGate.Require(Extractors.Wait(0), RefusalCode.LimitExceeded);
        try
        {
            return await Task.Run(() => Extract(program!, binding, av, subtitles,
                container, partitions), cancellation).ConfigureAwait(false);
        }
        catch (Exception e) when (e is JsonException or InvalidOperationException or KeyNotFoundException or
                                 FormatException or OverflowException or EditionRefused or IOException)
        {
            throw new Refused(RefusalCode.UnsupportedSubtitle);
        }
        finally { Extractors.Release(); }
    }

    private static PointerSubtitleSet Extract(string program, Binding b, MediaStream[] av, MediaStream[] subtitles,
        string container, MpvPartitions partitions)
    {
        // Probe exact integer packet clocks and payloads from the same verified local file. Bounded process, no shell,
        // no remote protocol or other output. The caller revalidates the same edition stamp after this read.
        var raw = Ffprobe.Run(program, ["-v", "quiet", "-protocol_whitelist", "file", "-f",
            container == "mkv" ? "matroska" : "mov", "-select_streams", "s", "-show_data", "-show_entries",
            "packet=stream_index,pts,duration,size,data:stream=index,codec_name,time_base,extradata,extradata_size:stream_disposition=default,forced",
            "-of", "json", "file:" + b.FilePath], TimeSpan.FromSeconds(8), MaxProbeBytes);
        using var doc = JsonDocument.Parse(raw);
        var stream = doc.RootElement.GetProperty("streams").EnumerateArray().ToDictionary(x => x.GetProperty("index").GetInt32());
        var packets = doc.RootElement.GetProperty("packets").EnumerateArray().ToLookup(x => x.GetProperty("stream_index").GetInt32());
        FixtureGate.Require(stream.Keys.Order().SequenceEqual(subtitles.Select(x => x.Index)) && packets.All(p => stream.ContainsKey(p.Key)),
            RefusalCode.UnsupportedSubtitle);
        var list = new List<SafeSubtitle>();
        var size = 0;
        foreach (var track in subtitles)
        {
            var info = stream[track.Index];
            var codec = MpvSubtitleCues.ForEmbeddedTrack(container, track.Codec);
            var flags = info.GetProperty("disposition");
            FixtureGate.Require(codec != null && codec == MpvSubtitleCues.ForEmbeddedTrack(container, info.GetProperty("codec_name").GetString()) &&
                flags.GetProperty("default").GetInt32() == (track.IsDefault ? 1 : 0) &&
                flags.GetProperty("forced").GetInt32() == (track.IsForced ? 1 : 0), RefusalCode.UnsupportedSubtitle);
            var timebase = info.GetProperty("time_base").GetString()!.Split('/');
            if (timebase.Length != 2) throw new FormatException();
            var ass = codec == EmbeddedTextCodec.Ass;
            var header = ass ? HexDump(info.GetProperty("extradata").GetString()!, info.GetProperty("extradata_size").GetInt32()) : [];
            var decision = MpvSubtitleCues.Render(b.Plan!, partitions, codec!.Value,
                long.Parse(timebase[0], NumberStyles.None, CultureInfo.InvariantCulture),
                long.Parse(timebase[1], NumberStyles.None, CultureInfo.InvariantCulture), header,
                packets[track.Index].Select(p => new SubtitlePacket(p.GetProperty("pts").GetInt64(),
                    p.TryGetProperty("duration", out var duration) ? duration.GetInt64() : 0,
                    HexDump(p.GetProperty("data").GetString()!, int.Parse(p.GetProperty("size").GetString()!,
                        NumberStyles.None, CultureInfo.InvariantCulture)))).ToArray());
            var bytes = decision.Value ?? throw new Refused(decision.Reason!.Value);
            if (bytes.Length == 0) continue;
            size += bytes.Length;
            FixtureGate.Require(size <= MaxSetBytes, RefusalCode.LimitExceeded);
            list.Add(new(new(av.Length + list.Count, ass, track.IsDefault, track.IsForced, track.Language, track.Title), bytes));
        }
        return new(av.Select(s => new MpvAvTrack(s.Codec!, s.Type == MediaStreamType.Video,
            s.Type == MediaStreamType.Audio && s.Index == b.Evidence!.AudioIndex, s.Language, s.Title)).ToArray(), list.ToArray());
    }

    // ffprobe -show_data uses offset, 8 words of 2 hex bytes each, then a fixed-width ASCII preview. Parse only
    // the exact declared byte count and verify row offsets; the preview can contain arbitrary subtitle text.
    private static byte[] HexDump(string text, int size)
    {
        if (size is < 0 or > MpvSubtitleCues.MaxTrackBytes) throw new FormatException();
        var bytes = new byte[size];
        var position = 0;
        foreach (var row in text.Split('\n', StringSplitOptions.RemoveEmptyEntries))
        {
            if (position >= size || row.Length < 51 || row[8..10] != ": " ||
                int.Parse(row.AsSpan(0, 8), NumberStyles.AllowHexSpecifier, CultureInfo.InvariantCulture) != position)
                throw new FormatException();
            var end = Math.Min(size, position + 16);
            for (var i = 0; position < end; ++i)
                bytes[position++] = byte.Parse(row.AsSpan(10 + i * 2 + i / 2, 2), NumberStyles.AllowHexSpecifier,
                    CultureInfo.InvariantCulture);
        }
        if (position != size) throw new FormatException();
        return bytes;
    }
}
