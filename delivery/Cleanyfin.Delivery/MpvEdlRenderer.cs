using System.Globalization;
using System.Text;

namespace Cleanyfin.Delivery;

public enum MpvClockMapping { Unverified, VerifiedSourceIdentity }

/// <summary>Opaque values generated externally. This adapter does not issue or authorize them.</summary>
public readonly record struct MpvSourceIdentifiers(UInt128 SessionId, UInt128 SourceTicket);

/// <summary>How picture and sound share the cut list.</summary>
public enum MpvPartitions
{
    /// <summary>One partition for all tracks, every boundary on its instant: mpv seeks sound with the picture's
    /// keyframe, which gives AAC/Opus decoder history after a join. A boundary on an instant is a tie (see
    /// <see cref="SplitPictureAndSound"/>), so no layout rendered this way is measured exact at every cut position.</summary>
    Joint,
    /// <summary>
    /// Picture and sound in separate partitions of the same source, each boundary placed where mpv's own rounding
    /// cannot tie. mpv 0.40 (filters/f_decoder_wrapper.c, audio/aframe.c mp_aframe_clip_timestamps) keeps a picture iff
    /// its time is at or after a segment start and before its end, and keeps or skips
    /// <c>floor((boundary - audio frame time) * rate)</c> samples of an audio frame: a sample survives a segment end, or
    /// is skipped by a segment start, iff its end instant is at or before the boundary. Whole-millisecond plan times sit
    /// exactly on 48 kHz sample instants, so the floor's input is an integer up to floating-point and container
    /// rounding (Matroska CodecDelay is stored in whole ns), and one kept sample is lost or one marked sample plays
    /// depending on the position. Here every picture boundary at a mark's edge sits
    /// <see cref="MpvEdlRenderer.PictureLeadMicroseconds"/> before its frame instant and every sound boundary at a
    /// mark's edge about half a sample after its sample instant; the presentation's own start and the end of the file
    /// stay where the unfiltered original starts and ends. Because each partition's start/end offsets balance, sound and
    /// picture keep the original's relative position on mpv's timeline after every join. The kept frames and samples
    /// are exactly those whose instants are outside the marks (measured: format-matrix.py cut-position sweep). Needs a
    /// sound rate that puts every whole millisecond on a sample and picture clocks with no tick inside the lead before a
    /// millisecond. Sound frames must decode without the picture partition's seek pre-roll (AC-3, E-AC-3, FLAC; not AAC
    /// or Opus, whose sound-only partition loses decoder history).
    /// </summary>
    SplitPictureAndSound,
}

public sealed record MpvAvTrack(string Codec, bool IsVideo, bool IsDefault, string? Language = null, string? Title = null);
public sealed record MpvSafeSubtitle(int Index, bool Ass, bool IsDefault, bool IsForced, string? Language, string? Title);
public sealed record MpvSubtitleLayout(IReadOnlyList<MpvAvTrack> AudioVideo, IReadOnlyList<MpvSafeSubtitle> Subtitles, UInt128 SubtitleTicket);

public static class MpvEdlRenderer
{
    public const string AdapterId = "mpv-edl";
    // 2: times on the file's own clock (source ms + the file time of source zero), joint form.
    // For a source whose zero is file time 0 rendered Joint, the bytes equal revision 1 apart from six decimals.
    public const string AdapterRevision = "2";
    // Separate evidence for delayed A/V and independently filtered subtitle partitions (joint A/V).
    public const string SubtitleAdapterRevision = "3";
    // 4/5: the tie-free split form (picture lead, sound half a sample late), without and with subtitle partitions.
    public const string SplitAdapterRevision = "4";
    public const string SplitSubtitleAdapterRevision = "5";
    // Far above floating-point and container rounding (nanoseconds), far below a 48 kHz sample (20.8 us) or a frame.
    public const long PictureLeadMicroseconds = 2;

    /// <summary>The adapter revision an evidence tuple must name for this cut-list form.</summary>
    public static string Revision(MpvPartitions partitions, bool subtitles) => (partitions, subtitles) switch
    {
        (MpvPartitions.SplitPictureAndSound, false) => SplitAdapterRevision,
        (MpvPartitions.SplitPictureAndSound, true) => SplitSubtitleAdapterRevision,
        (_, false) => AdapterRevision,
        (_, true) => SubtitleAdapterRevision,
    };

    public static bool IsSubtitleRevision(string? revision) => revision is SubtitleAdapterRevision or SplitSubtitleAdapterRevision;

    /// <summary>Half a sample period in whole microseconds (rounded down), or null if a whole millisecond is not a
    /// sample instant at this rate, or the picture lead plus half a sample would not stay well inside one sample (the
    /// sound-before-picture offset of a presentation that starts at a mark's end, where both partitions start at the
    /// shifted edge).</summary>
    public static long? SoundLagMicroseconds(int sampleRate)
    {
        if (sampleRate is < 8000 or > 192000 || sampleRate % 1000 != 0)
            return null;
        var lag = 500_000L / sampleRate;
        // At least 1 us of margin on both sides of lag + lead inside one sample period.
        return lag >= 1 && (lag + PictureLeadMicroseconds + 1) * sampleRate <= 1_000_000 ? lag : null;
    }

    /// <summary>True if no tick of this picture clock lies in [m - lead, m) for any whole millisecond m, so moving a
    /// boundary the picture lead earlier can never drop a kept frame. A tick's distance below a millisecond is a
    /// multiple of gcd(1000 * den, 1e6 * num) / den microseconds.</summary>
    public static bool PictureClockClearsLead(long numerator, long denominator)
    {
        if (numerator <= 0 || denominator <= 0)
            return false;
        var step = System.Numerics.BigInteger.GreatestCommonDivisor((System.Numerics.BigInteger)1000 * denominator,
            (System.Numerics.BigInteger)1_000_000 * numerator);
        return step > (System.Numerics.BigInteger)PictureLeadMicroseconds * denominator;
    }

    public static Decision<byte[]> Render(PlaybackPlan plan, Uri trustedBaseOrigin, MpvSourceIdentifiers ids,
        MpvClockMapping mapping, bool noChapters = false, MpvPartitions partitions = MpvPartitions.Joint,
        MpvSubtitleLayout? subtitles = null, int soundSampleRate = 0)
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
        if (!TryBuildSlotUrl(trustedBaseOrigin, ids.SessionId, out var slotUrl))
            return Decision<byte[]>.Refuse(RefusalCode.InvalidOrigin);

        if (FileZeroMicroseconds(plan.Source) is not { } zero)
            return Decision<byte[]>.Refuse(RefusalCode.UnverifiedPlayerClock);
        var split = partitions == MpvPartitions.SplitPictureAndSound;
        long lag = 0;
        if (split)
        {
            if (SoundLagMicroseconds(soundSampleRate) is not { } l || !plan.Source.Tracks.Where(t => t.Kind == "video")
                    .All(t => PictureClockClearsLead(t.TimeBaseNumerator, t.TimeBaseDenominator)))
                return Decision<byte[]>.Refuse(RefusalCode.UnverifiedPlayerClock);
            lag = l;
        }

        var output = new StringBuilder("# mpv EDL v0\n");
        if (noChapters)
            output.Append("!no_chapters\n");
        var source = FormattableString.Invariant($"{slotUrl}/source?t={ids.SourceTicket:x32}");
        var quotedSource = Quote(source);
        if (subtitles is { } layout)
        {
            if (layout.SubtitleTicket == 0 ||
                layout.AudioVideo.Count is < 2 or > 16 || layout.AudioVideo.Count(t => t.IsVideo) != 1 ||
                layout.AudioVideo.Any(t => t.Codec.Length is < 1 or > 32 ||
                    t.Codec.Any(c => !char.IsAsciiLetterOrDigit(c) && c != '_')) ||
                layout.Subtitles.Count > 16 || layout.Subtitles.Any(t => t.Index < 0) ||
                layout.Subtitles.Select(t => t.Index).Distinct().Count() != layout.Subtitles.Count)
                return Decision<byte[]>.Refuse(RefusalCode.UnsupportedSubtitle);
            // Describe only A/V from the original, in source order within each partition.
            if (split)
            {
                foreach (var track in layout.AudioVideo.Where(t => t.IsVideo))
                    AppendTrack("video", track.Codec, track.IsDefault, lang: track.Language, title: track.Title);
                Segments(PictureSegments());
                output.Append("!new_stream\n");
                foreach (var track in layout.AudioVideo.Where(t => !t.IsVideo))
                    AppendTrack("audio", track.Codec, track.IsDefault, lang: track.Language, title: track.Title);
                Segments(FileSegments(plan, zero, lag, 0));
            }
            else
            {
                foreach (var track in layout.AudioVideo)
                    AppendTrack(track.IsVideo ? "video" : "audio", track.Codec, track.IsDefault, lang: track.Language, title: track.Title);
                Segments(PictureSegments());
            }
            // Subtitle partitions run on the picture's presentation clock.
            var duration = PictureSegments().Sum(s => s.End - s.Start);
            foreach (var track in layout.Subtitles)
            {
                output.Append("!new_stream\n");
                AppendTrack("sub", track.Ass ? "ass" : "subrip", track.IsDefault, track.IsForced, track.Language, track.Title);
                var url = FormattableString.Invariant($"{slotUrl}/subtitle-{track.Index}.{(track.Ass ? "ass" : "srt")}?t={layout.SubtitleTicket:x32}");
                output.Append(Quote(url)).Append(",0,").Append(Seconds(duration)).Append('\n');
            }
            return Decision<byte[]>.Allow(Encoding.UTF8.GetBytes(output.ToString()));
        }
        if (split)
        {
            output.Append("!delay_open,media_type=video\n");
            Segments(PictureSegments());
            output.Append("!new_stream\n!delay_open,media_type=audio\n");
            Segments(FileSegments(plan, zero, lag, 0));
        }
        else
            Segments(PictureSegments());
        return Decision<byte[]>.Allow(Encoding.UTF8.GetBytes(output.ToString()));

        IEnumerable<(long Start, long End)> PictureSegments() => FileSegments(plan, zero, PictureShift(partitions), 0);

        void Segments(IEnumerable<(long Start, long End)> segments)
        {
            foreach (var (start, end) in segments)
                output.Append(quotedSource).Append(',').Append(Seconds(start))
                    .Append(',').Append(Seconds(end - start)).Append('\n');
        }

        void AppendTrack(string type, string codec, bool isDefault, bool isForced = false, string? lang = null, string? title = null)
        {
            output.Append("!delay_open,media_type=").Append(type).Append(",codec=").Append(codec).Append('\n');
            var flags = (isDefault, isForced) switch
            {
                (true, true) => "default+forced", (true, false) => "default", (false, true) => "forced", _ => null
            };
            lang = SafeMeta(lang);
            title = SafeMeta(title);
            if (flags is null && lang is null && title is null) return;
            output.Append("!track_meta");
            if (flags != null) output.Append(",flags=").Append(flags);
            if (lang != null) output.Append(",lang=").Append(Quote(lang));
            if (title != null) output.Append(",title=").Append(Quote(title));
            output.Append('\n');
        }
    }

    /// <summary>Shift of interior picture boundaries for this form: the presentation clock subtitles follow.</summary>
    public static long PictureShift(MpvPartitions partitions) =>
        partitions == MpvPartitions.SplitPictureAndSound ? -PictureLeadMicroseconds : 0;

    /// <summary>Kept ranges as file-clock segments in microseconds, shared with the subtitle sidecars. Interior
    /// boundaries (a mark's edges) move by <paramref name="interiorShift"/>; the presentation's first start (source 0,
    /// clipped at file time 0) moves by <paramref name="leadingShift"/>; the end of the file never moves.</summary>
    internal static IEnumerable<(long Start, long End)> FileSegments(PlaybackPlan plan, long zero, long interiorShift,
        long leadingShift = 0)
    {
        foreach (var kept in plan.Timeline.KeptRanges)
        {
            // A kept range that ends at or before file time 0 (Matroska sound priming before the first picture, left
            // over when a mark starts on the first picture) has nothing mpv can present. Rendering it gave a negative
            // picture segment and mpv refused the whole cut list; drop it from every partition alike.
            if (kept.SourceEndMs * 1000 + zero <= 0 && kept.SourceEndMs != plan.Source.DurationMs)
                continue;
            var start = kept.SourceStartMs == 0
                ? Math.Max(zero, 0) + leadingShift
                : Math.Max(kept.SourceStartMs * 1000 + zero + interiorShift, 0);
            var end = kept.SourceEndMs * 1000 + zero + (kept.SourceEndMs == plan.Source.DurationMs ? 0 : interiorShift);
            yield return (start, end);
        }
    }

    private static string? SafeMeta(string? s) => s is { Length: > 0 and <= 128 } && !s.Any(char.IsControl) ? s : null;
    private static string Quote(string s) => FormattableString.Invariant($"%{Encoding.UTF8.GetByteCount(s)}%{s}");

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
    private static bool TryBuildSlotUrl(Uri origin, UInt128 sessionId, out string result)
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
            $"{authority}{path.TrimEnd('/')}/Cleanyfin/play/{sessionId:x32}");
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
