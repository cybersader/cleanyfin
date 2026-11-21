using System;
using System.Collections.Immutable;
using System.IO;
using System.Linq;
using System.Net;
using System.Security.Cryptography;
using System.Threading.Tasks;
using Cleanyfin.Delivery;
using Jellyfin.Data;
using Jellyfin.Data.Queries;
using MediaBrowser.Controller;
using Jellyfin.Database.Implementations.Enums;
using Jellyfin.Plugin.Cleanyfin.Configuration;
using MediaBrowser.Controller.Devices;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Net;
using MediaBrowser.Controller.Session;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.Library;
using MediaBrowser.Model.MediaInfo;
using Microsoft.AspNetCore.Http;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

internal sealed class Refused(RefusalCode reason) : Exception { internal RefusalCode Reason => reason; }
internal sealed record Binding(string Session, Guid User, string Device, string TokenHash, PluginConfiguration Config,
    Guid? Pointer, string Client, string Version, string Item, string Source, string Digest, string Revision, string PlanHash, DateTimeOffset Expires);

// Not a resolver. These facts are ONLY for the reviewed synthetic kit. The runner hashes the
// generated original before exposing it as one immutable, read-only file mount at FixturePath.
// A writable/racing host asset is not supported. No plugin code opens media for delivery.
internal sealed class FixtureGate(ILibraryManager library, IDeviceManager devices, ISessionManager sessions,
    IAuthorizationContext auth, GrantService grants, IUserManager users, IServerApplicationHost host)
{
    internal const string FixturePath = "/fixture-library/source.mp4";
    private const string PlanPath = "/cleanyfin-fixture/plan.json";
    private const string Revision = "local:plan:42";
    internal static PluginConfiguration Config => Plugin.Instance?.Configuration is { EnableAuthRangeLab: true } c
        && c.LabLifetimeSeconds is > 0 and <= 1800 ? c : throw new Refused(RefusalCode.AbsentPlan);

    internal async Task<(Binding Binding, long Ticks)> Prepare(HttpContext ctx, Guid? pointer = null)
    {
        var c = Config;
        Require((ctx.Request.PathBase.Value ?? string.Empty) == Origin(c).AbsolutePath.TrimEnd('/'), RefusalCode.InvalidOrigin);
        var a = await auth.GetAuthorizationInfo(ctx);
        Require(a.IsAuthenticated && !a.IsApiKey && a.User != null && !string.IsNullOrEmpty(a.Token), RefusalCode.AuthorityMismatch);
        Require(pointer.HasValue ? a.Client == "Wholphin" && a.Version == "1.1.2-0-gc530f5c5"
            : a.Client == "auth-range" && a.Version == "1", RefusalCode.AuthorityMismatch);
        // After a restart a valid native login can outlive the in-memory session list. Resolve this
        // request's own authenticated token through the host (never manufacture a principal).
        var session = await sessions.GetSessionByAuthenticationToken(a.Token!, a.DeviceId!, ctx.Connection.RemoteIpAddress?.ToString() ?? "");
        Require(session != null && session.UserId == a.UserId && session.DeviceId == a.DeviceId && session.Client == a.Client &&
            session.ApplicationVersion == a.Version, RefusalCode.AuthorityMismatch);
        var binding = new Binding(session!.Id, a.UserId, a.DeviceId!, GrantTable<Binding>.Hash(a.Token!), c,
            pointer, a.Client!, a.Version!, c.LabItemId, c.LabItemId, c.LabSourceSha256, Revision, c.LabPlanSha256, DateTimeOffset.MaxValue);
        var (plan, _) = Validate(binding, ctx);
        var origin = Origin(c);
        Require(MpvEdlRenderer.Render(plan, origin, new(1,1), MpvClockMapping.VerifiedSourceIdentity).Accepted, RefusalCode.InvalidOrigin);
        var expires = DateTimeOffset.UtcNow.AddSeconds(c.LabLifetimeSeconds);
        binding = binding with { Expires = expires < plan.ExpiresAt ? expires : plan.ExpiresAt };
        return (binding, checked(plan.Timeline.FilteredDurationMs * 10_000));
    }

    internal string Issue(Binding binding)
    {
        var origin = Origin(binding.Config);
        var reason = grants.Table.Issue(binding, binding.Expires, out var slot, out var list);
        if (reason.HasValue) throw new Refused(reason.Value);
        return $"{origin.OriginalString.TrimEnd('/')}/Cleanyfin/play/{slot}/list.edl?t={list}";
    }

    internal (PlaybackPlan Plan, string Token) Validate(Binding b, HttpContext ctx)
    {
        var c = Config;
        var ip = ctx.Connection.RemoteIpAddress;
        Require(ip != null && IPAddress.IsLoopback(ip.IsIPv4MappedToIPv6 ? ip.MapToIPv4() : ip) &&
            host.ApplicationVersion.ToString(3) == "10.11.11", RefusalCode.UnverifiedBinding);
        Require(ReferenceEquals(c, b.Config) && c.LabItemId == b.Item && c.LabSourceSha256 == b.Digest && c.LabPlanSha256 == b.PlanHash, RefusalCode.BindingMismatch);
        Require(b.Expires > DateTimeOffset.UtcNow && sessions.Sessions.Any(s => s.Id == b.Session && s.UserId == b.User && s.DeviceId == b.Device && s.Client == b.Client && s.ApplicationVersion == b.Version), RefusalCode.AuthorityMismatch);
        var device = devices.GetDevices(new DeviceQuery { UserId = b.User, DeviceId = b.Device }).Items.SingleOrDefault(d => GrantTable<Binding>.Hash(d.AccessToken) == b.TokenHash);
        Require(device != null, RefusalCode.AuthorityMismatch);
        var user = users.GetUserById(b.User);
        Require(user != null && !user.HasPermission(PermissionKind.IsDisabled) && !user.HasPermission(PermissionKind.IsAdministrator)
            && user.IsParentalScheduleAllowed() && devices.CanAccessDevice(user, b.Device), RefusalCode.AuthorityMismatch);
        Require(Guid.TryParseExact(b.Item, "N", out var id), RefusalCode.BindingMismatch);
        var item = library.GetItemById<Video>(id, user!);
        Require(item != null && item.IsVisibleStandalone(user!) && item.GetPlayAccess(user!) == PlayAccess.Full &&
            library.GetCollectionFolders(item).Any(f => library.GetUserRootFolder().GetChildren(user!, true).Any(v => v.Id == f.Id)), RefusalCode.AuthorityMismatch);
        Require(c.LabViewerId == b.User.ToString("N") && c.LabDeviceId == b.Device, RefusalCode.AuthorityMismatch);
        if (b.Pointer is Guid pid)
        {
            var pointer = library.GetItemById<Video>(pid, user!);
            Require(!c.LabWithholdProjection && pid.ToString("N") == c.LabPointerId && pointer != null &&
                PointerFixture.Owned(pointer, c) && pointer.RunTimeTicks == PointerFixture.Ticks &&
                pointer.IsVisibleStandalone(user!) && pointer.GetPlayAccess(user!) == PlayAccess.Full &&
                !user!.HasPermission(PermissionKind.ForceRemoteSourceTranscoding) &&
                library.GetCollectionFolders(pointer).Any(f => library.GetUserRootFolder().GetChildren(user, true).Any(v => v.Id == f.Id)), RefusalCode.AuthorityMismatch);
        }
        Require(item!.Path == FixturePath && !item.IsShortcut && !item.HasLocalAlternateVersions && item.LinkedAlternateVersions.Length == 0, RefusalCode.UnverifiedBinding);
        // A file mount pins the inode across host pathname replacement. This lab additionally
        // owns the host writer and never changes that inode in place. Do not generalize to folders.
        Require(OperatingSystem.IsLinux() && File.ReadLines("/proc/self/mountinfo").Any(line =>
            line.Split(' ') is [_, _, _, _, FixturePath, var options, ..] && options.Split(',').Contains("ro")), RefusalCode.UnverifiedBinding);
        var sources = item.GetMediaSources(false);
        var src = sources.Count == 1 ? sources[0] : null;
        Require(src != null && src.Id == b.Source && src.Path == FixturePath && src.Protocol == MediaProtocol.File &&
            src.VideoType == VideoType.VideoFile && !src.IsInfiniteStream && !src.RequiresOpening && src.SupportsDirectPlay && src.Size > 0 &&
            src.RunTimeTicks == 200_000_000 && src.MediaStreams.Count == 2 &&
            src.MediaStreams.Any(s => s.Index == 0 && s.Type == MediaStreamType.Video && s.Codec == "h264") &&
            src.MediaStreams.Any(s => s.Index == 1 && s.Type == MediaStreamType.Audio && s.Codec == "aac"), RefusalCode.BindingMismatch);
        var context = new VerifiedPlanContext(b.Revision, new("auth-range", b.Item, b.Source, b.Digest, src!.Size!.Value,
            "review:fixture:1", "binding:3", 20_000, PlanValidator.SourceOrigin, [new(0,"video",1,12800,0,0), new(1,"audio",1,48000,0,0)]),
            new(c.LabViewerId,"local:p2","policy:7","trust:2"), "home:review:9", PlanValidator.ReviewedCoverage,
            ImmutableDictionary<string,string>.Empty.Add("home:fixture:mark1", "06abcdc0c8b60fb497da40742ad1c354015cb99c06126a5353dbff6e5cdcb792"));
        using var file = File.OpenRead(PlanPath); // Fixed plan file, never a media path.
        Require(file.Length is > 0 and <= PlanValidator.MaxPlanBytes, RefusalCode.PlanTooLarge);
        var bytes = new byte[file.Length]; file.ReadExactly(bytes);
        Require(file.ReadByte() == -1 && Convert.ToHexStringLower(SHA256.HashData(bytes)) == b.PlanHash, RefusalCode.RevisionMismatch);
        var decision = PlanValidator.Parse(bytes, TimeProvider.System, context);
        return (decision.Value ?? throw new Refused(decision.Reason!.Value), device!.AccessToken);
    }

    internal static Uri Origin(PluginConfiguration c) => Uri.TryCreate(c.LabOrigin, UriKind.Absolute, out var u)
        && u.Scheme == "http" && (u.Host == "127.0.0.1" || (u.Host == "10.0.2.2" && GrantTable<Binding>.IsHandle(c.LabPointerKey))) && u.Query == "" && u.Fragment == "" && u.UserInfo == "" ? u : throw new Refused(RefusalCode.InvalidOrigin);
    internal static void Require(bool condition, RefusalCode reason) { if (!condition) throw new Refused(reason); }
}
