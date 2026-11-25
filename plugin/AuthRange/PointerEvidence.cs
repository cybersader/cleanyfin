using System;
using System.IO;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Cleanyfin.Delivery;
using Jellyfin.Plugin.Cleanyfin.Configuration;
using MediaBrowser.Controller.Net;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

internal sealed record EvidencePermit(EvidenceTuple Tuple, EvidenceStatus Status, string RegisterHash, int AudioIndex);

internal static class PointerEvidence
{
    private const string RegisterName = "evidence-register-v1.json";
    private static readonly Lazy<string> PluginBuild = new(() =>
    {
        using var dll = File.OpenRead(typeof(Plugin).Assembly.Location);
        return Convert.ToHexStringLower(SHA256.HashData(dll));
    });
    private static readonly Regex HeaderPart = new(@"\A *([A-Za-z]+)=""([^""\r\n]*)"" *\z", RegexOptions.NonBacktracking);

    // IAuthorizationContext fills missing fields from the token's stored device metadata.
    // Require current MediaBrowser claims as well as the host-authenticated matching session;
    // this is a cooperative compatibility signal and does NOT authenticate a binary.
    internal static bool HasCurrentClaims(HttpContext ctx, AuthorizationInfo native)
    {
        var headers = ctx.Request.Headers;
        if (headers.Authorization.Count != 1 || headers.Authorization[0] is not { Length: > 13 and <= 4096 } raw ||
            !raw.StartsWith("MediaBrowser ", StringComparison.OrdinalIgnoreCase) || headers.ContainsKey("X-Emby-Authorization") ||
            headers.ContainsKey("X-Emby-Token") || headers.ContainsKey("X-MediaBrowser-Token")) return false;
        var fields = new System.Collections.Generic.Dictionary<string, string>(StringComparer.Ordinal);
        // Only accept unambiguous quoted parameters; commas in values must be encoded.
        foreach (var part in raw[13..].Split(','))
        {
            var match = HeaderPart.Match(part);
            if (!match.Success || !fields.TryAdd(match.Groups[1].Value, WebUtility.UrlDecode(match.Groups[2].Value))) return false;
        }
        return Matches("Client", native.Client) && Matches("DeviceId", native.DeviceId) &&
            Matches("Version", native.Version) && Matches("Token", native.Token);

        bool Matches(string name, string? value) => !string.IsNullOrWhiteSpace(value) && fields.TryGetValue(name, out var declared) && declared == value;
    }

    internal static EvidencePermit Select(HttpContext ctx, Binding binding, PlaybackPlan plan, MediaSourceInfo source,
        string backend, string serverVersion)
    {
        var (appBuild, outputMode) = Enrollment(binding.User);
        // The exact measured stream layout of the verified original: every track's codec, profile, bit depth, range,
        // geometry, rate, channels, time base and clock anchor, and which sound track plays. Evidence for one layout
        // never covers another; anything the label cannot state is refused here.
        var described = Describe(source, plan);
        var hasSubtitles = source.MediaStreams.Any(s => s.Type == MediaStreamType.Subtitle);
        FixtureGate.Require(!hasSubtitles || source.MediaStreams.Where(s => s.Type == MediaStreamType.Subtitle)
            .All(s => MpvSubtitleCues.ForEmbeddedTrack(described.Source.Container, s.Codec).HasValue), RefusalCode.UnsupportedSubtitle);
        var revision = hasSubtitles ? MpvEdlRenderer.SubtitleAdapterRevision : MpvEdlRenderer.AdapterRevision;
        var tuple = new EvidenceTuple(MpvEdlRenderer.AdapterId, revision, binding.Client, binding.Version,
            appBuild, backend, outputMode, serverVersion, PluginBuild.Value, described.Source);
        return Admit(ctx, binding.Config, tuple, plan, described.AudioIndex);
    }

    /// <summary>
    /// The cut-list form each measured layout class needs (tools/playback-lab/format-matrix.py). Sound without
    /// inter-frame overlap (FLAC): split picture and sound partitions. Matroska with AC-3/E-AC-3: one partition, interior
    /// boundaries just before the instant (Matroska frame times read a hair early). Everything else: one partition on
    /// the instant. A layout this does not fit is still refused by the register; this only picks the form.
    /// </summary>
    internal static MpvPartitions Partitions(EvidencePermit? permit) => permit?.Tuple.Source switch
    {
        { AudioCodec: "flac" } => MpvPartitions.SplitPictureAndSound,
        { Container: "mkv", AudioCodec: "ac3" or "eac3" } => MpvPartitions.JointBeforeInstant,
        _ => MpvPartitions.Joint,
    };

    /// <summary>The evidence source of a catalogue media source on a plan's clock, or <see cref="Refused"/>.</summary>
    internal static DescribedSource Describe(MediaSourceInfo source, PlaybackPlan plan)
    {
        var facts = source.MediaStreams.Where(s => !s.IsExternal).OrderBy(s => s.Index).Select(s => new SourceTrackFacts(s.Index,
            s.Type switch { MediaStreamType.Video => "video", MediaStreamType.Audio => "audio", MediaStreamType.Subtitle => "subtitle", _ => "other" },
            s.Codec ?? "", s.Profile, s.Width, s.Height, s.RealFrameRate, s.BitDepth,
            s.Type == MediaStreamType.Video ? s.VideoRangeType.ToString() : null, s.Channels, s.SampleRate, s.IsDefault, s.IsForced,
            s.Language)).ToArray();
        FixtureGate.Require(source.MediaStreams.All(s => !s.IsExternal) && source.MediaAttachments.Count == 0, RefusalCode.EvidenceTupleUnknown);
        var described = EvidenceSourceLabel.Describe(source.Container, facts, plan.Source.Tracks, plan.Source.DurationMs);
        return described.Value ?? throw new Refused(described.Reason!.Value);
    }

    internal static void Recheck(HttpContext ctx, Binding binding, PlaybackPlan plan, EvidencePermit previous)
    {
        var (appBuild, outputMode) = Enrollment(binding.User);
        FixtureGate.Require(appBuild == previous.Tuple.AppBuild && outputMode == previous.Tuple.DeviceMode,
            RefusalCode.EvidenceIdentityUnavailable);
        FixtureGate.Require(Admit(ctx, binding.Config, previous.Tuple, plan, previous.AudioIndex) == previous, RefusalCode.RevisionMismatch);
    }

    private static EvidencePermit Admit(HttpContext ctx, PluginConfiguration cfg, EvidenceTuple tuple, PlaybackPlan plan, int audioIndex)
    {
        var (register, hash) = Load(ctx);
        var lab = cfg.EnableAuthRangeLab && !cfg.EnableProductionEligibility;
        var decision = register.Admit(tuple, plan.Requires, allowExperimental: lab);
        if (decision.Reason == RefusalCode.EvidenceTupleUnknown)
            // Lab diagnostics: the layout and app facts are not secret (the client knows them); no ids, paths or tokens.
            ctx.RequestServices.GetRequiredService<ILogger<PointerPlaybackFilter>>().LogInformation(
                "Evidence tuple unknown: {App} {Version} {Backend} on {Server}; source {Container} {Video} {Audio} {DurationMs} ms: {Layout}",
                tuple.AppId, tuple.AppVersion, tuple.Backend, tuple.JellyfinVersion, tuple.Source.Container, tuple.Source.VideoCodec,
                tuple.Source.AudioCodec, tuple.Source.DurationMs, tuple.Source.TrackLayout);
        var row = decision.Value ?? throw new Refused(decision.Reason!.Value);
        // This host seam cannot distinguish decoder/output modes or authenticate the app build.
        // A local declaration is acceptable only for an explicit synthetic lab enrollment.
        FixtureGate.Require(lab, RefusalCode.EvidenceModeUnverified);
        FixtureGate.Require(cfg.LabSourceSha256 == plan.Source.Sha256,
            RefusalCode.EvidenceTupleUnknown);
        return new(tuple, row.Status, hash, audioIndex);
    }

    private static (string AppBuild, string DeviceMode) Enrollment(Guid viewer)
    {
        try
        {
            var path = Path.Combine(Plugin.Instance!.ShelfRoot, "profiles", viewer.ToString("N"), "evidence-device.txt");
            ShelfStore.NoLinks(path);
            using var stream = File.OpenRead(path);
            var lines = new UTF8Encoding(false, true).GetString(ReadBounded(stream, 512)).TrimEnd('\n', '\r').Split('\n');
            if (lines is [var hash, var mode] && hash.Length == 64 && hash.All(c => char.IsAsciiDigit(c) || c is >= 'a' and <= 'f') &&
                mode.Length is > 0 and <= 256 && !string.IsNullOrWhiteSpace(mode) && !mode.Any(char.IsControl)) return (hash, mode);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or DecoderFallbackException or Refused) { }
        throw new Refused(RefusalCode.EvidenceIdentityUnavailable);
    }

    private static (EvidenceRegister Register, string Hash) Load(HttpContext ctx)
    {
        RefusalCode cause;
        try
        {
            var path = Path.Combine(Plugin.Instance!.ShelfRoot, RegisterName);
            ShelfStore.NoLinks(path);
            using var stream = File.OpenRead(path);
            var bytes = ReadBounded(stream, EvidenceRegister.MaxDocumentBytes);
            var result = EvidenceRegister.Parse(bytes);
            if (result.Value is { } register) return (register, Convert.ToHexStringLower(SHA256.HashData(bytes)));
            cause = result.Reason!.Value;
        }
        catch (Refused e) { cause = e.Reason; }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { cause = RefusalCode.EvidenceRegisterInvalid; }
        ctx.RequestServices.GetRequiredService<ILogger<PointerPlaybackFilter>>().LogWarning("Evidence register rejected: {Reason}", cause);
        throw new Refused(RefusalCode.EvidenceRegisterInvalid);
    }

    private static byte[] ReadBounded(Stream stream, int limit)
    {
        FixtureGate.Require(stream.Length is > 0 && stream.Length <= limit, RefusalCode.LimitExceeded);
        var bytes = new byte[stream.Length];
        stream.ReadExactly(bytes);
        FixtureGate.Require(stream.ReadByte() == -1, RefusalCode.LimitExceeded);
        return bytes;
    }
}
