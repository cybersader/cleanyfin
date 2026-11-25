using System.Collections.Immutable;
using System.Globalization;
using System.Text;

namespace Cleanyfin.Delivery;

/// <summary>What the server's catalogue says about one embedded stream of the verified original.</summary>
public sealed record SourceTrackFacts(int Index, string Kind, string Codec, string? Profile = null, int? Width = null,
    int? Height = null, double? FrameRate = null, int? BitDepth = null, string? Range = null, int? Channels = null,
    int? SampleRate = null, bool IsDefault = false, bool IsForced = false, string? Language = null);

/// <summary>The measured stream layout a playback is offered for: the evidence tuple's source, plus the sound track it plays.</summary>
public sealed record DescribedSource(EvidenceSource Source, int AudioIndex);

/// <summary>
/// Turns a verified original into the exact <see cref="EvidenceSource"/> an evidence row must name. The label records
/// everything that changed exactness in the lab: container family, every track's codec, profile, bit depth, range,
/// geometry, rate, channel count, time base and clock anchor, which sound track plays and which subtitles exist.
/// Two files that differ in any of these get different labels, so evidence never transfers between them. Anything
/// the label cannot state (no or ambiguous default sound, other stream kinds, unreadable facts) is refused, never guessed.
/// </summary>
public static class EvidenceSourceLabel
{
    public const int MaxLabelLength = 256;

    /// <summary>MP4 family (mov, mp4, m4a, 3gp, 3g2, mj2) as "mp4"; Matroska (mkv, matroska) as "mkv"; else null.</summary>
    public static string? ContainerFamily(string? names)
    {
        var parts = names?.Split(',', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
        if (parts is not { Length: > 0 })
            return null;
        if (parts.All(n => n is "mov" or "mp4" or "m4a" or "3gp" or "3g2" or "mj2"))
            return "mp4";
        if (parts.All(n => n is "mkv" or "matroska"))
            return "mkv";
        return null;
    }

    /// <summary>Whether a client profile's container list offers this family (a profile may list other formats too).</summary>
    public static bool Offers(string? profileContainers, string family) =>
        profileContainers?.Split(',', StringSplitOptions.TrimEntries).Any(n => ContainerFamily(n) == family) == true;

    public static Decision<DescribedSource> Describe(string? container, IReadOnlyList<SourceTrackFacts> tracks,
        ImmutableArray<TrackClock> clocks, long durationMs)
    {
        var family = ContainerFamily(container);
        if (family is null || tracks.Count is 0 or > 16 || durationMs <= 0 || clocks.IsDefault)
            return Decision<DescribedSource>.Refuse(RefusalCode.EvidenceTupleUnknown);
        if (tracks.Select(t => t.Index).Distinct().Count() != tracks.Count ||
            tracks.Any(t => t.Kind is not ("video" or "audio" or "subtitle") || !Token(t.Codec)))
            return Decision<DescribedSource>.Refuse(RefusalCode.EvidenceTupleUnknown);
        var video = tracks.Where(t => t.Kind == "video").ToArray();
        var audio = tracks.Where(t => t.Kind == "audio").ToArray();
        if (video.Length != 1 || audio.Length == 0)
            return Decision<DescribedSource>.Refuse(RefusalCode.EvidenceTupleUnknown);
        // The track a player selects by itself: the single default-flagged one, or the only one.
        var defaults = audio.Where(a => a.IsDefault).ToArray();
        var selected = defaults.Length == 1 ? defaults[0] : audio.Length == 1 ? audio[0] : null;
        if (selected is null)
            return Decision<DescribedSource>.Refuse(RefusalCode.EvidenceTupleUnknown);

        var text = new StringBuilder();
        foreach (var t in tracks.OrderBy(t => t.Index))
        {
            if (text.Length > 0)
                text.Append(", ");
            text.Append(CultureInfo.InvariantCulture, $"{t.Index} {t.Kind} {t.Codec}");
            if (t.Kind != "subtitle")
            {
                var clock = clocks.SingleOrDefault(c => c.Index == t.Index && c.Kind == t.Kind);
                if (clock is null || (t.Profile is { } p && !Words(p)))
                    return Decision<DescribedSource>.Refuse(RefusalCode.EvidenceTupleUnknown);
                if (t.Profile is { Length: > 0 } profile)
                    text.Append(' ').Append(profile);
                if (t.Kind == "video")
                {
                    if (t is not { Width: > 0, Height: > 0, FrameRate: > 0, BitDepth: > 0 } || t.Range is not { } range || !Token(range))
                        return Decision<DescribedSource>.Refuse(RefusalCode.EvidenceTupleUnknown);
                    text.Append(CultureInfo.InvariantCulture,
                        $" {t.BitDepth} bit {range} {t.Width}x{t.Height} {Math.Round(t.FrameRate!.Value, 3):0.###} fps");
                }
                else
                {
                    if (t is not { Channels: > 0, SampleRate: > 0 })
                        return Decision<DescribedSource>.Refuse(RefusalCode.EvidenceTupleUnknown);
                    text.Append(CultureInfo.InvariantCulture, $" {t.Channels} ch {t.SampleRate} Hz");
                }
                text.Append(CultureInfo.InvariantCulture,
                    $" tb {clock.TimeBaseNumerator}/{clock.TimeBaseDenominator} at {clock.AnchorSourceMs} ms");
                if (ReferenceEquals(t, selected))
                    text.Append(" plays");
            }
            else
            {
                if (t.IsDefault) text.Append(" default");
                if (t.IsForced)
                {
                    text.Append(" forced");
                    if (t.Language is { Length: > 0 } lang && Token(lang) &&
                        string.Equals(lang, selected.Language, StringComparison.OrdinalIgnoreCase))
                        text.Append(" matches-audio");
                }
            }
        }
        // Every audio/video clock of the edition must be accounted for by the catalogue, and vice versa.
        if (clocks.Count(c => c.Kind is "video" or "audio") != video.Length + audio.Length)
            return Decision<DescribedSource>.Refuse(RefusalCode.EvidenceTupleUnknown);
        var label = text.ToString();
        if (label.Length > MaxLabelLength)
            return Decision<DescribedSource>.Refuse(RefusalCode.EvidenceTupleUnknown);
        return Decision<DescribedSource>.Allow(new(new(family, video[0].Codec, selected.Codec, label, durationMs), selected.Index));
    }

    private static bool Token(string s) => s.Length is > 0 and <= 32 && s.All(c => char.IsAsciiLetterOrDigit(c) || c is '_' or '-' or '.');

    private static bool Words(string s) => s.Length <= 48 && s.All(c => char.IsAsciiLetterOrDigit(c) || c is ' ' or ':' or '.' or '-' or '_') &&
        !s.StartsWith(' ') && !s.EndsWith(' ');
}
