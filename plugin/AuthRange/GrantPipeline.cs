using System;
using System.Globalization;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Cleanyfin.Delivery;
using MediaBrowser.Controller.Net;
using MediaBrowser.Controller.Library;
using Jellyfin.Data.Events;
using Jellyfin.Database.Implementations.Entities;
using MediaBrowser.Controller.Session;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Http.Features;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Controllers;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

internal sealed class GrantService : IDisposable
{
    internal GrantTable<Binding> Table { get; } = new(TimeProvider.System);
    internal SemaphoreSlim Slots { get; } = new(16);
    private readonly ISessionManager _sessions;
    private readonly IUserManager _users;
    internal GrantService(ISessionManager sessions, IUserManager users)
    {
        _sessions = sessions; _users = users;
        sessions.SessionEnded += End; users.OnUserUpdated += UserChanged;
    }
    private void UserChanged(object? _, GenericEventArgs<User> e) => Table.Revoke(b => b.User == e.Argument.Id);
    private void End(object? sender, SessionEventArgs args) => Table.Revoke(b => b.Session == args.SessionInfo.Id);
    public void Dispose() { _sessions.SessionEnded -= End; _users.OnUserUpdated -= UserChanged; Slots.Dispose(); }
}

internal sealed record Admission(Binding Binding, Leg Leg, byte[]? Content, bool Ass = false);
internal sealed class GrantPipeline : IStartupFilter
{
    internal static readonly object Marker = new();
    public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next) => app =>
    {
        app.Use(Invoke);
        next(app);
    };

    internal static void Private(HttpContext ctx)
    {
        ctx.Response.Headers.CacheControl = "private, no-store";
        ctx.Response.Headers["Referrer-Policy"] = "no-referrer";
    }

    internal static void Deny(HttpContext ctx, RefusalCode reason)
    {
        ctx.RequestServices.GetRequiredService<ILogger<GrantPipeline>>().LogInformation("Auth-range lab refused: {Reason}", reason);
        Private(ctx); ctx.Response.StatusCode = 404; ctx.Response.ContentLength = 0;
    }

    private static async Task Invoke(HttpContext ctx, RequestDelegate next)
    {
        var c = Plugin.Instance?.Configuration;
        var path = ctx.Request.Path.Value ?? "";
        if (!FixtureGate.DeliveryEnabled(c) || !Uri.TryCreate(c!.LabOrigin, UriKind.Absolute, out var origin)) { await next(ctx); return; }
        var prefix = origin.AbsolutePath.TrimEnd('/');
        var stem = prefix + "/Cleanyfin/play/";
        if (!path.StartsWith(stem, StringComparison.Ordinal)) { await next(ctx); return; }
        var route = path[stem.Length..].Split('/');
        var svc = ctx.RequestServices.GetRequiredService<GrantService>();
        if (!svc.Slots.Wait(0)) { Deny(ctx, RefusalCode.LimitExceeded); return; }
        try
        {
            var query = ctx.Request.QueryString.Value;
            if (route.Length != 2 || query is not { Length: 35 } || !query.StartsWith("?t=", StringComparison.Ordinal))
                throw new Refused(RefusalCode.InvalidIdentifier);
            var leg = route[1] switch
            {
                "source" => Leg.Source, "list.edl" => Leg.List,
                var s when s.StartsWith("subtitle-", StringComparison.Ordinal) && s.Length <= 15 => Leg.Subtitle,
                _ => throw new Refused(RefusalCode.InvalidIdentifier),
            };
            if (GrantTable<Binding>.IsHandle(c.LabPointerKey))
            {
                var trace = ctx.RequestServices.GetRequiredService<ILogger<GrantPipeline>>();
                ctx.Response.OnCompleted(() => {
                    trace.LogInformation("Fixture {Method} {Route} {Status}", ctx.Request.Method,
                        leg switch { Leg.List => "/Cleanyfin/play/{S}/list.edl", Leg.Subtitle => "/Cleanyfin/play/{S}/subtitle-{N}",
                            _ => "/Cleanyfin/play/{S}/source -> /Videos/{original}/stream" }, ctx.Response.StatusCode);
                    return Task.CompletedTask;
                });
            }
            var ticket = query[3..];
            var reason = svc.Table.Check(route[0], ticket, leg, ctx.Request.Method, out var binding);
            if (reason.HasValue) throw new Refused(reason.Value);
            var b = binding!;
            var gate = ctx.RequestServices.GetRequiredService<FixtureGate>();
            PlaybackPlan plan;
            string token;
            try { (plan, token) = gate.Validate(b, ctx); }
            catch { svc.Table.Revoke(row => ReferenceEquals(row, b)); throw; }
            // No ambient identity needed. Reject cookies and ambiguous credentials rather than
            // combining them. A single independently supplied native header must match the issuer.
            var h = ctx.Request.Headers;
            var credentialHeaders = new[] { "Authorization", "X-Emby-Authorization", "X-Emby-Token", "X-MediaBrowser-Token" }.Where(h.ContainsKey).ToArray();
            FixtureGate.Require(!h.ContainsKey("Cookie") && credentialHeaders.Length <= 1, RefusalCode.AuthorityMismatch);
            if (credentialHeaders.Length == 1)
            {
                var a = await ctx.RequestServices.GetRequiredService<IAuthorizationContext>().GetAuthorizationInfo(ctx.Request);
                FixtureGate.Require(h[credentialHeaders[0]].Count == 1 && a.IsAuthenticated && !a.IsApiKey && a.UserId == b.User && a.Token == token && a.DeviceId == b.Device, RefusalCode.AuthorityMismatch);
            }
            byte[]? content = null;
            var ass = false;
            if (leg == Leg.List)
            {
                var d = MpvEdlRenderer.Render(plan, FixtureGate.Origin(c), new(UInt128.Parse(route[0], NumberStyles.HexNumber, CultureInfo.InvariantCulture), svc.Table.SourceFor(ticket)),
                    MpvClockMapping.VerifiedSourceIdentity, noChapters:b.Shelf!=null, partitions:PointerEvidence.Partitions(b.Evidence),
                    subtitles: b.Subtitles?.Layout(svc.Table.SubtitleFor(ticket)));
                content = d.Value ?? throw new Refused(d.Reason!.Value);
                var accept = ctx.Request.GetTypedHeaders().Accept;
                if (accept is { Count: > 0 } && !accept.Any(m => (m.Quality ?? 1) > 0 && (m.MediaType.Value?.ToLowerInvariant() is "*/*" or "text/*" or "text/plain")))
                { Private(ctx); ctx.Response.StatusCode = 406; return; }
            }
            if (leg == Leg.Subtitle)
            {
                var sub = b.Subtitles?.Safe.SingleOrDefault(s => route[1] == FormattableString.Invariant(
                    $"subtitle-{s.Track.Index}.{(s.Track.Ass ? "ass" : "srt")}")) ?? throw new Refused(RefusalCode.InvalidIdentifier);
                content = sub.Bytes;
                ass = sub.Track.Ass;
            }
            ctx.Items[Marker] = new Admission(b, leg, content, ass);
            ctx.Request.QueryString = leg != Leg.Source ? QueryString.Empty : new($"?static=true&mediaSourceId={b.Source}");
            ctx.Request.Path = prefix + (leg switch
            {
                Leg.List => "/Cleanyfin/lab/descriptor", Leg.Subtitle => "/Cleanyfin/lab/subtitle", _ => $"/Videos/{b.Item}/stream"
            });
            // The raw-target feature is independent of Path and QueryString. Do not leave
            // a second copy of the capability available to downstream native middleware.
            if (ctx.Features.Get<IHttpRequestFeature>() is {} feature)
                feature.RawTarget = ctx.Request.Path + ctx.Request.QueryString;
            foreach (var name in h.Keys.ToArray())
                if (!new[] { "Host", "Range", "If-Range", "If-Match", "If-None-Match", "If-Modified-Since", "If-Unmodified-Since" }.Contains(name, StringComparer.OrdinalIgnoreCase)) h.Remove(name);
            h.Host = origin.Authority;
            h.Authorization = $"MediaBrowser Token=\"{token}\"";
            ctx.Response.OnStarting(() => { Private(ctx); return Task.CompletedTask; });
            // Best effort for a transfer still writing to the socket. Cannot recall buffered bytes.
            var sessions = ctx.RequestServices.GetRequiredService<ISessionManager>();
            void End(object? _, SessionEventArgs e) { if (e.SessionInfo.Id == b.Session) ctx.Abort(); }
            using var deadline = new CancellationTokenSource();
            var remaining = b.Expires - DateTimeOffset.UtcNow;
            if (remaining <= TimeSpan.Zero) throw new Refused(RefusalCode.Expired);
            deadline.CancelAfter(remaining);
            using var abort = deadline.Token.Register(ctx.Abort);
            sessions.SessionEnded += End;
            try { await next(ctx); }
            finally { sessions.SessionEnded -= End; h.Remove("Authorization"); }
        }
        catch (Exception ex) when (!ctx.Response.HasStarted)
        { Deny(ctx, ex is Refused refused ? refused.Reason : RefusalCode.UnverifiedBinding); }
        finally { svc.Slots.Release(); }
    }
}

// A final check at the native action, after the host's authentication and routing. Neither
// get-first-source fallback nor a FileStreamResult, redirect or remote result may serve bytes.
internal sealed class GrantActionFilter : IAsyncActionFilter
{
    public async Task OnActionExecutionAsync(ActionExecutingContext action, ActionExecutionDelegate next)
    {
        var ctx = action.HttpContext;
        if (ctx.Items[GrantPipeline.Marker] is not Admission a) { await next(); return; }
        var identity = await ctx.RequestServices.GetRequiredService<IAuthorizationContext>().GetAuthorizationInfo(ctx);
        var isTarget = action.ActionDescriptor is ControllerActionDescriptor cad && (a.Leg switch
        {
            Leg.List => cad.ControllerTypeInfo.AsType() == typeof(AuthRangeController) && cad.ActionName == nameof(AuthRangeController.Descriptor),
            Leg.Subtitle => cad.ControllerTypeInfo.AsType() == typeof(AuthRangeController) && cad.ActionName == nameof(AuthRangeController.Subtitle),
            _ => cad.ControllerTypeInfo.FullName == "Jellyfin.Api.Controllers.VideosController" && cad.MethodInfo.Name == "GetVideoStream",
        });
        if (!isTarget || ctx.User.Identity?.IsAuthenticated != true || !identity.IsAuthenticated || identity.IsApiKey || identity.UserId != a.Binding.User || identity.DeviceId != a.Binding.Device || GrantTable<Binding>.Hash(identity.Token ?? "") != a.Binding.TokenHash)
        { GrantPipeline.Deny(ctx, RefusalCode.AuthorityMismatch); action.Result = new EmptyResult(); return; }
        var result = await next();
        if (a.Leg == Leg.Source && (result.Result is not PhysicalFileResult { EnableRangeProcessing: true } file || file.FileName != a.Binding.FilePath))
        { GrantPipeline.Deny(ctx, RefusalCode.BindingMismatch); result.Result = new EmptyResult(); }
    }
}
