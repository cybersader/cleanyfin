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

internal sealed record EvidencePermit(EvidenceTuple Tuple, EvidenceStatus Status, string RegisterHash);

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
        var video = source.MediaStreams.Single(s => s.Type == MediaStreamType.Video);
        var audio = source.MediaStreams.Single(s => s.Type == MediaStreamType.Audio);
        // PinnedAsset checks exact track indexes, source clocks, no sidecars and no extra streams.
        // These observed bounds apply only to the hard fixture; a generic MP4 is not a match.
        var layout = video is { Width:1280, Height:720, AverageFrameRate:25, RealFrameRate:25 } &&
            audio is { Channels:2, SampleRate:48000, Profile:"LC" }
            ? "0 video 1280x720 25 fps 1/12800, 1 stereo AAC-LC 48 kHz 1/48000, source presentation zero, no extra tracks"
            : throw new Refused(RefusalCode.EvidenceTupleUnknown);
        var tuple = new EvidenceTuple(MpvEdlRenderer.AdapterId, MpvEdlRenderer.AdapterRevision, binding.Client, binding.Version,
            appBuild, backend, outputMode, serverVersion, PluginBuild.Value,
            new("mp4", video.Codec, audio.Codec, layout, plan.Source.DurationMs));
        return Admit(ctx, binding.Config, tuple, plan);
    }

    internal static void Recheck(HttpContext ctx, Binding binding, PlaybackPlan plan, EvidencePermit previous)
    {
        var (appBuild, outputMode) = Enrollment(binding.User);
        FixtureGate.Require(appBuild == previous.Tuple.AppBuild && outputMode == previous.Tuple.DeviceMode,
            RefusalCode.EvidenceIdentityUnavailable);
        FixtureGate.Require(Admit(ctx, binding.Config, previous.Tuple, plan) == previous, RefusalCode.RevisionMismatch);
    }

    private static EvidencePermit Admit(HttpContext ctx, PluginConfiguration cfg, EvidenceTuple tuple, PlaybackPlan plan)
    {
        var (register, hash) = Load(ctx);
        var lab = cfg.EnableAuthRangeLab && !cfg.EnableProductionEligibility;
        var decision = register.Admit(tuple, plan.Requires, allowExperimental: lab);
        var row = decision.Value ?? throw new Refused(decision.Reason!.Value);
        // This host seam cannot distinguish decoder/output modes or authenticate the app build.
        // A local declaration is acceptable only for an explicit synthetic lab enrollment.
        FixtureGate.Require(lab, RefusalCode.EvidenceModeUnverified);
        FixtureGate.Require(cfg.LabSourceSha256 == plan.Source.Sha256,
            RefusalCode.EvidenceTupleUnknown);
        return new(tuple, row.Status, hash);
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
            using var stream = OpenRegister(path);
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

    private static Stream OpenRegister(string path)
    {
        try { return File.OpenRead(path); }
        catch (Exception e) when (e is FileNotFoundException or DirectoryNotFoundException)
        {
            return typeof(Plugin).Assembly.GetManifestResourceStream("Cleanyfin.Evidence.Register.v1") ??
                throw new Refused(RefusalCode.EvidenceRegisterInvalid);
        }
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
