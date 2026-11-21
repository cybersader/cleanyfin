using System.Globalization;
using System.Text;

namespace Cleanyfin.Delivery;

public enum MpvClockMapping { Unverified, VerifiedSourceIdentity }

/// <summary>Opaque values generated externally. This adapter does not issue or authorize them.</summary>
public readonly record struct MpvSourceIdentifiers(UInt128 SessionId, UInt128 SourceTicket);

public static class MpvEdlRenderer
{
    public static Decision<byte[]> Render(PlaybackPlan plan, Uri trustedBaseOrigin, MpvSourceIdentifiers ids,
        MpvClockMapping mapping, bool noChapters = false)
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

        var output = new StringBuilder("# mpv EDL v0\n");
        if (noChapters)
            output.Append("!no_chapters\n");
        var quotedSource = FormattableString.Invariant($"%{Encoding.UTF8.GetByteCount(source)}%{source}");
        foreach (var kept in plan.Timeline.KeptRanges)
        {
            output.Append(quotedSource).Append(',').Append(Seconds(kept.SourceStartMs))
                .Append(',').Append(Seconds(kept.LengthMs)).Append('\n');
        }
        return Decision<byte[]>.Allow(Encoding.UTF8.GetBytes(output.ToString()));
    }

    private static string Seconds(long milliseconds) =>
        FormattableString.Invariant($"{milliseconds / 1000}.{milliseconds % 1000:D3}");

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
