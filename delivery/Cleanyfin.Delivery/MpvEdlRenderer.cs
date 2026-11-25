using System.Globalization;
using System.Text;

namespace Cleanyfin.Delivery;

public enum MpvClockMapping { Unverified, VerifiedSourceIdentity }

/// <summary>Opaque values generated externally. This adapter does not issue or authorize them.</summary>
public readonly record struct MpvSourceIdentifiers(UInt128 SessionId, UInt128 SourceTicket);

/// <summary>How picture and sound share the cut list.</summary>
public enum MpvPartitions
{
    /// <summary>One partition for all tracks: mpv seeks sound with the picture's keyframe, which gives overlapping
    /// transform codecs (AAC, AC-3, E-AC-3, Opus) the decoder history they need after a join.</summary>
    Joint,
    /// <summary>Picture and sound in separate partitions of the same source, picture boundaries a quarter sample before
    /// each instant and sound boundaries a quarter sample after it. The kept frames and samples are the same sets; it
    /// removes mpv's boundary rounding (a marked frame or sample at a tie). Only for sound with no inter-frame overlap
    /// (FLAC): an overlapping codec loses its history in a sound-only partition.</summary>
    SplitPictureAndSound,
    /// <summary>One partition with every interior boundary a quarter sample before its instant. mpv reads Matroska
    /// frame times a hair early, so a boundary exactly on a frame instant keeps the marked frame; this moves the
    /// boundary off the tie. Only where sound was measured to keep the same samples with it (AC-3, E-AC-3 in Matroska).</summary>
    JointBeforeInstant,
}

public static class MpvEdlRenderer
{
    public const string AdapterId = "mpv-edl";
    // 2: times on the file's own clock (source ms + the file time of source zero) and the split-partition form.
    // For a source whose zero is file time 0 rendered Joint, the bytes equal revision 1 apart from six decimals.
    public const string AdapterRevision = "2";
    // A quarter of a 48 kHz sample, rounded to whole microseconds (5.208 us); well under half a frame or sample.
    private const long SplitOffsetMicroseconds = 5;

    public static Decision<byte[]> Render(PlaybackPlan plan, Uri trustedBaseOrigin, MpvSourceIdentifiers ids,
        MpvClockMapping mapping, bool noChapters = false, MpvPartitions partitions = MpvPartitions.Joint)
    {
        ArgumentNullException.ThrowIfNull(plan);
        if (plan.Requires.Any(feature => feature != PlanValidator.Cut))
            return Decision<byte[]>.Refuse(RefusalCode.UnsupportedFeature);
        if (mapping != MpvClockMapping.VerifiedSourceIdentity)
            return Decision<byte[]>.Refuse(RefusalCode.UnverifiedPlayerClock);
        if (plan.Timeline.KeptRanges.IsEmpty)
            return Decision<byte[]>.Refuse(RefusalCode.NoKeptRanges);
        if (plan.Operations.IsEmpty)
            return Decision<byte[]>.Refuse(RefusalCode.NoCuts);
        if (ids.SessionId == 0 || ids.SourceTicket == 0)
            return Decision<byte[]>.Refuse(RefusalCode.InvalidIdentifier);
        if (!TryBuildSourceUrl(trustedBaseOrigin, ids, out var source))
            return Decision<byte[]>.Refuse(RefusalCode.InvalidOrigin);

        if (FileZeroMicroseconds(plan.Source) is not { } zero)
            return Decision<byte[]>.Refuse(RefusalCode.UnverifiedPlayerClock);

        var output = new StringBuilder("# mpv EDL v0\n");
        if (noChapters)
            output.Append("!no_chapters\n");
        var quotedSource = FormattableString.Invariant($"%{Encoding.UTF8.GetByteCount(source)}%{source}");
        if (partitions == MpvPartitions.SplitPictureAndSound)
        {
            output.Append("!delay_open,media_type=video\n");
            Segments(-SplitOffsetMicroseconds);
            output.Append("!new_stream\n!delay_open,media_type=audio\n");
            Segments(SplitOffsetMicroseconds);
        }
        else
            Segments(partitions == MpvPartitions.JointBeforeInstant ? -SplitOffsetMicroseconds : 0);
        return Decision<byte[]>.Allow(Encoding.UTF8.GetBytes(output.ToString()));

        // mpv reads segment times as the file's own timestamps; the plan's source zero is the earliest audio/video
        // first timestamp, which may be before (MKV encoder priming) or after file time zero. Interior boundaries move
        // by `shift`; the source's own start and end do not.
        void Segments(long shift)
        {
            foreach (var kept in plan.Timeline.KeptRanges)
            {
                var start = kept.SourceStartMs * 1000 + zero + (kept.SourceStartMs == 0 ? 0 : shift);
                var end = kept.SourceEndMs * 1000 + zero + (kept.SourceEndMs == plan.Source.DurationMs ? 0 : shift);
                start = Math.Max(start, 0); // nothing before file time 0 is presented
                output.Append(quotedSource).Append(',').Append(Seconds(start))
                    .Append(',').Append(Seconds(end - start)).Append('\n');
            }
        }
    }

    /// <summary>
    /// The file time of source zero in whole microseconds, from the verified track clocks: every audio/video track's
    /// first timestamp minus its anchor must agree exactly. Null (refuse) if they disagree or it is not a whole microsecond.
    /// </summary>
    public static long? FileZeroMicroseconds(SourceBinding source)
    {
        long? zero = null;
        foreach (var t in source.Tracks.Where(t => t.Kind is "video" or "audio"))
        {
            var numerator = (System.Numerics.BigInteger)t.AnchorPts * t.TimeBaseNumerator * 1_000_000;
            if (!System.Numerics.BigInteger.Remainder(numerator, t.TimeBaseDenominator).IsZero)
                return null;
            var value = (long)(numerator / t.TimeBaseDenominator) - t.AnchorSourceMs * 1000;
            if (zero is { } z && z != value)
                return null;
            zero = value;
        }
        return zero;
    }

    private static string Seconds(long microseconds) => microseconds < 0
        ? "-" + Seconds(-microseconds)
        : microseconds % 1000 == 0
            ? FormattableString.Invariant($"{microseconds / 1_000_000}.{microseconds / 1000 % 1000:D3}")
            : FormattableString.Invariant($"{microseconds / 1_000_000}.{microseconds % 1_000_000:D6}");

    // trustedBaseOrigin is deployment configuration (not a request Host, plan field, or endpoint).
    // Preserve literal UTF-8 in vetted base-path segments for mpv's length-prefixed fields.
    private static bool TryBuildSourceUrl(Uri origin, MpvSourceIdentifiers ids, out string result)
    {
        result = string.Empty;
        if (!origin.IsAbsoluteUri || origin.Scheme is not ("http" or "https") ||
            origin.HostNameType is not (UriHostNameType.Dns or UriHostNameType.IPv4 or UriHostNameType.IPv6) ||
            origin.UserInfo.Length != 0 || origin.Query.Length != 0 || origin.Fragment.Length != 0)
            return false;

        var raw = origin.OriginalString;
        if (raw.Length > 2048 || raw.Any(c => char.IsControl(c) || char.IsWhiteSpace(c) ||
            c is '\\' or '#' or '?' or '@' or '%'))
            return false;
        var authorityStart = raw.IndexOf("://", StringComparison.Ordinal);
        if (authorityStart != origin.Scheme.Length)
            return false;
        authorityStart += 3;
        var pathStart = raw.IndexOf('/', authorityStart);
        if (pathStart == authorityStart || authorityStart >= raw.Length)
            return false;
        var path = pathStart < 0 ? string.Empty : raw[pathStart..];
        if (path.Contains("//", StringComparison.Ordinal))
            return false;
        foreach (var segment in path.Trim('/').Split('/', StringSplitOptions.RemoveEmptyEntries))
        {
            if (segment is "." or ".." || !IsBasePathSegment(segment))
                return false;
        }

        var authority = origin.GetLeftPart(UriPartial.Authority);
        result = FormattableString.Invariant(
            $"{authority}{path.TrimEnd('/')}/Cleanyfin/play/{ids.SessionId:x32}/source?t={ids.SourceTicket:x32}");
        return Uri.TryCreate(result, UriKind.Absolute, out var generated) &&
            generated.Scheme == origin.Scheme && generated.IdnHost == origin.IdnHost &&
            generated.Port == origin.Port && generated.UserInfo.Length == 0 && generated.Fragment.Length == 0;
    }

    private static bool IsBasePathSegment(string segment)
    {
        foreach (var rune in segment.EnumerateRunes())
        {
            if (!Rune.IsLetterOrDigit(rune) && Rune.GetUnicodeCategory(rune) is not UnicodeCategory.NonSpacingMark
                && !(rune.IsAscii && "-._~,;".Contains((char)rune.Value)))
                return false;
        }
        return true;
    }
}
