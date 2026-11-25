using System.Globalization;
using System.Numerics;
using System.Text;

namespace Cleanyfin.Delivery;

public enum EmbeddedTextCodec { SubRip, Ass, MovText }
public sealed record SubtitlePacket(long Pts, long Duration, byte[] Data);

/// <summary>R19: compare on the verified source clock before rendering or rounding any surviving cue.</summary>
public static class MpvSubtitleCues
{
    public const int MaxTrackBytes = 256 * 1024;
    private static readonly UTF8Encoding Utf8 = new(false, true);
    private const string AssFormat = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text";

    public static EmbeddedTextCodec? ForEmbeddedTrack(string? container, string? codec) => (container, codec) switch
    {
        ("mkv", "subrip" or "srt") => EmbeddedTextCodec.SubRip,
        ("mkv", "ass") => EmbeddedTextCodec.Ass,
        ("mp4", "mov_text") => EmbeddedTextCodec.MovText,
        _ => null // PGS/VobSub, other text formats and unknown codecs are never an A/V-only fallback.
    };

    /// <summary>A memory-only SRT or ASS sidecar on the filtered presentation clock; empty if no cue survives.</summary>
    public static Decision<byte[]> Render(PlaybackPlan plan, MpvPartitions partitions, EmbeddedTextCodec codec,
        long timeBaseNumerator, long timeBaseDenominator, byte[] privateData, IReadOnlyList<SubtitlePacket> packets)
    {
        if (timeBaseNumerator <= 0 || timeBaseDenominator <= 0 || packets.Count > 8192 ||
            MpvEdlRenderer.FileZeroMicroseconds(plan.Source) is not { } zero)
            return Decision<byte[]>.Refuse(RefusalCode.UnsupportedSubtitle);
        try
        {
            var denominator = (BigInteger)timeBaseDenominator;
            var tick = (BigInteger)timeBaseNumerator * 1_000_000; // rational microseconds on the file clock
            var ass = codec == EmbeddedTextCodec.Ass;
            var output = new StringBuilder(ass ? AssHeader(privateData) : "");
            var segments = MpvEdlRenderer.FileSegments(plan, zero, MpvEdlRenderer.PictureShift(partitions)).ToArray();
            Require(segments.Length > 0 && segments.All(s => s.End > s.Start));
            var kept = 0;
            foreach (var packet in packets.OrderBy(p => p.Pts))
            {
                var data = packet.Data.AsSpan();
                if (codec == EmbeddedTextCodec.MovText)
                {
                    // tx3g's two-byte text length. Empty samples delimit gaps, not cues. No styled sample boxes yet.
                    Require(data.Length >= 2 && System.Buffers.Binary.BinaryPrimitives.ReadUInt16BigEndian(data) == data.Length - 2);
                    data = data[2..];
                    if (data.IsEmpty)
                        continue;
                }
                Require(packet.Duration > 0 && data.Length is > 0 and <= MaxTrackBytes);
                var a = packet.Pts * tick;
                var b = a + packet.Duration * tick;
                var sourceA = a - zero * denominator;
                var sourceB = b - zero * denominator;
                // Closed contact is intentionally suppressed on either edge, even when it occupies no marked time.
                if (plan.Operations.Any(mark => sourceA <= mark.EndMs * 1000 * denominator &&
                                                sourceB >= mark.StartMs * 1000 * denominator))
                    continue;
                long elapsed = 0;
                var unit = denominator * (ass ? 10_000 : 1000);
                var found = false;
                foreach (var (start, end) in segments)
                {
                    if (a < end * denominator && b > start * denominator)
                    {
                        // Clip only at the presentation's actual first/last sample, not at a mark. Round inward to
                        // the sidecar format's clock (ASS centiseconds, SRT milliseconds), after the exact decision.
                        var lo = BigInteger.Max(a - start * denominator, 0) + elapsed * denominator;
                        var hi = BigInteger.Min(b - start * denominator, (end - start) * denominator) + elapsed * denominator;
                        var begin = (long)((lo + unit - 1) / unit);
                        var finish = (long)(hi / unit);
                        Require(!found && finish > begin);
                        var text = Utf8.GetString(data);
                        if (ass) AppendAss(output, begin, finish, text);
                        else AppendSrt(output, kept + 1, begin, finish, text);
                        Require(output.Length <= MaxTrackBytes);
                        found = true;
                        kept++;
                    }
                    elapsed = checked(elapsed + end - start);
                }
                Require(found); // an unsupported packet/presentation mapping is not an offer with missing dialogue
            }
            if (kept == 0) return Decision<byte[]>.Allow([]);
            Require(Utf8.GetByteCount(output.ToString()) <= MaxTrackBytes);
            return Decision<byte[]>.Allow(Utf8.GetBytes(output.ToString()));
        }
        catch (Exception e) when (e is FormatException or DecoderFallbackException or OverflowException)
        {
            return Decision<byte[]>.Refuse(RefusalCode.UnsupportedSubtitle);
        }
    }

    private static string AssHeader(byte[] bytes)
    {
        Require(bytes.Length is > 0 and <= MaxTrackBytes);
        var text = Normalize(Utf8.GetString(bytes)).TrimStart('﻿');
        // CodecPrivate is header/styles only. Never let it add untimed or unexamined dialogue, fonts or graphics.
        var section = "";
        var lines = text.Split('\n');
        var events = false;
        foreach (var raw in lines)
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith(';')) continue;
            if (line.StartsWith('['))
            {
                var next = line.ToLowerInvariant();
                Require(!events && next is "[script info]" or "[v4+ styles]" or "[events]");
                section = next;
                events = section == "[events]";
            }
            else
            {
                Require(section.Length != 0 && !line.StartsWith("Dialogue:", StringComparison.OrdinalIgnoreCase) &&
                    (!events || line.Equals(AssFormat, StringComparison.OrdinalIgnoreCase)));
            }
        }
        // Replace the event-format line, if present, with the one we actually serialize.
        var header = string.Join('\n', lines.TakeWhile(l => !l.Trim().Equals("[Events]", StringComparison.OrdinalIgnoreCase))).TrimEnd();
        return header + "\n\n[Events]\n" + AssFormat + "\n";
    }

    private static void AppendAss(StringBuilder output, long begin, long finish, string text)
    {
        // Matroska SSA/ASS packet layout: ReadOrder,Layer,Style,Name,MarginL,MarginR,MarginV,Effect,Text.
        Require(!text.Any(char.IsControl));
        var fields = text.Split(',', 9);
        Require(fields.Length == 9 && int.TryParse(fields[0], NumberStyles.None, CultureInfo.InvariantCulture, out _) &&
            int.TryParse(fields[1], NumberStyles.None, CultureInfo.InvariantCulture, out _) && fields[8].Length != 0);
        output.Append(CultureInfo.InvariantCulture,
            $"Dialogue: {fields[1]},{Time(begin, true)},{Time(finish, true)},{string.Join(',', fields, 2, 7)}\n");
    }

    private static void AppendSrt(StringBuilder output, int number, long begin, long finish, string text)
    {
        text = Normalize(text);
        // No separator inside a cue: it must not be possible for payload text to introduce its own timing line.
        Require(!text.Contains("-->", StringComparison.Ordinal) && text.Split('\n').All(l => !string.IsNullOrWhiteSpace(l)));
        output.Append(CultureInfo.InvariantCulture, $"{number}\n{Time(begin, false)} --> {Time(finish, false)}\n{text}\n\n");
    }

    private static string Normalize(string text)
    {
        text = text.Replace("\r\n", "\n", StringComparison.Ordinal);
        Require(!text.Any(c => char.IsControl(c) && c is not ('\n' or '\t')));
        return text;
    }

    private static string Time(long value, bool ass)
    {
        var scale = ass ? 100 : 1000;
        var seconds = value / scale;
        return ass ? FormattableString.Invariant($"{seconds / 3600}:{seconds / 60 % 60:D2}:{seconds % 60:D2}.{value % scale:D2}")
            : FormattableString.Invariant($"{seconds / 3600:D2}:{seconds / 60 % 60:D2}:{seconds % 60:D2},{value % scale:D3}");
    }

    private static void Require(bool valid) { if (!valid) throw new FormatException(); }
}
