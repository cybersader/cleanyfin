using System;
using System.Linq;
using System.Threading.Tasks;
using Cleanyfin.Delivery;
using MediaBrowser.Model.Dlna;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.MediaInfo;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Controllers;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

// Version-pinned native action wrapper. Never register a replacement route or call the API over HTTP.
internal sealed class PointerPlaybackFilter : IAsyncActionFilter
{
    public async Task OnActionExecutionAsync(ActionExecutingContext action, ActionExecutionDelegate next)
    {
        var c = Plugin.Instance?.Configuration;
        if (action.ActionDescriptor is not ControllerActionDescriptor cad || cad.ControllerTypeInfo.FullName != "Jellyfin.Api.Controllers.MediaInfoController" ||
            cad.MethodInfo.Name is not ("GetPlaybackInfo" or "GetPostedPlaybackInfo") ||
            !action.ActionArguments.TryGetValue("itemId",out var raw) || raw is not Guid id)
        { await next(); return; }
        var ctx = action.HttpContext;
        // Identity check, not authorization. A mistaken private mapping must not intercept an ordinary item.
        var library = ctx.RequestServices.GetRequiredService<MediaBrowser.Controller.Library.ILibraryManager>();
        var store=ctx.RequestServices.GetRequiredService<ShelfStore>();
        if (library.GetItemById(id) is not MediaBrowser.Controller.Entities.Video { IsShortcut:true } video || !store.Identified(video))
        { await next(); return; }
        var denied = new OkObjectResult(new PlaybackInfoResponse { ErrorCode = PlaybackErrorCode.NoCompatibleStream });
        GrantPipeline.Private(ctx);
        ctx.Response.Headers["X-Cleanyfin-Reason"]=RefusalCode.UnsupportedFeature.ToString();
        var trace = ctx.RequestServices.GetRequiredService<ILogger<PointerPlaybackFilter>>();
        ctx.Response.OnCompleted(() => { trace.LogInformation("Fixture {Method} /Items/{{pointer}}/PlaybackInfo {Status}",ctx.Request.Method,ctx.Response.StatusCode); return Task.CompletedTask; });
        ActionExecutedContext? executed = null;
        try
        {
            if (!FixtureGate.PointerEnabled(c)) throw new Refused(RefusalCode.EvidenceDisabled);
            if (c!.LabWithholdProjection || ctx.User.Identity?.IsAuthenticated != true || ctx.Request.Query.Count != 0 ||
                cad.MethodInfo.Name != "GetPostedPlaybackInfo" || !action.ActionArguments.TryGetValue("playbackInfoDto", out var dto) ||
                dto?.GetType().FullName != "Jellyfin.Api.Models.MediaInfoDtos.PlaybackInfoDto") { action.Result = denied; return; }
            // The bound DTO resides in Jellyfin.Api (not its plugin SDK). Missing pinned members refuse.
            object? Field(string name) => (dto.GetType().GetProperty(name) ?? throw new MissingMemberException(name)).GetValue(dto);
            if (Field("DeviceProfile") is not DeviceProfile { Name: { Length: > 0 and <= 256 } backend } profile)
                throw new Refused(RefusalCode.EvidenceIdentityUnavailable);
            if (Field("MediaSourceId") is string selected && (!Guid.TryParse(selected,out var selectedId) || selectedId != id) ||
                Field("LiveStreamId") != null || Field("UserId") != null || Field("EnableDirectPlay") is false ||
                Field("AudioStreamIndex") is not (null or int) || Field("SubtitleStreamIndex") is not (null or -1))
            { action.Result = denied; return; }
            var gate = ctx.RequestServices.GetRequiredService<FixtureGate>();
            var (binding,ticks) = await gate.Prepare(ctx,id,backend); // both ACLs, immutable original, exact evidence tuple
            var permit = binding.Evidence!; var measured = permit.Tuple.Source;
            // Only the measured layout as it plays by itself: its own default sound track, no subtitle selection by the server.
            if (Field("AudioStreamIndex") is int chosen && chosen != permit.AudioIndex) { action.Result = denied; return; }
            if (profile.ContainerProfiles.Length != 0 || profile.CodecProfiles.Length != 0 ||
                !profile.DirectPlayProfiles.Any(p => p.Type == DlnaProfileType.Video && EvidenceSourceLabel.Offers(p.Container, measured.Container) &&
                    p.VideoCodec?.Split(',').Contains(measured.VideoCodec) == true && p.AudioCodec?.Split(',').Contains(measured.AudioCodec) == true))
            { action.Result = denied; return; }
            // Run the real POST for the original, with this principal/profile/bitrate/track choice.
            // Its result never leaves MVC: replace it BEFORE inspecting or issuing a capability.
            action.ActionArguments["itemId"] = Guid.ParseExact(binding.Item,"N");
            action.ActionArguments["mediaSourceId"] = binding.Source;
            action.ActionArguments["autoOpenLiveStream"] = false;
            executed = await next();
            var native = executed.Result;
            executed.Result = denied;
            if (executed.Exception != null || native is not ObjectResult { Value: PlaybackInfoResponse { ErrorCode:null, MediaSources:[var src] } info } ||
                !src.SupportsDirectPlay || src.TranscodingUrl != null || src.Id != binding.Source || (src.Path!=binding.FilePath || !DirectFile(src) || PointerEvidence.Describe(src, binding.Plan!).Source != measured) ||
                Field("MaxStreamingBitrate") is int max && !(src.Bitrate <= max) ||
                Field("MaxAudioChannels") is int channels && !(src.MediaStreams.Single(x=>x.Index==permit.AudioIndex).Channels <= channels)) return;
            gate.Validate(binding,ctx);
            var evidence = permit.Status;
            var projected = new MediaSourceInfo { Id=id.ToString("N"), Protocol=MediaProtocol.Http, IsRemote=true,
                Name=evidence == EvidenceStatus.Experimental ? "Cleanyfin sample (experimental)" : "Cleanyfin filtered",
                SupportsDirectPlay=true, SupportsDirectStream=false, SupportsTranscoding=false, Container=measured.Container, RunTimeTicks=ticks,
                Bitrate=src.Bitrate, DefaultAudioStreamIndex=permit.AudioIndex, MediaStreams=PointerFixture.Streams(src), Path=gate.Issue(binding) };
            ctx.Response.Headers.Remove("X-Cleanyfin-Reason");
            ctx.Response.Headers["X-Cleanyfin-Evidence"] = evidence == EvidenceStatus.Experimental ? "experimental" : "eligible";
            executed.Result = new OkObjectResult(new PlaybackInfoResponse { MediaSources=[projected], PlaySessionId=info.PlaySessionId });
        }
        catch (Exception ex) // only a bounded reason, never exception text or URL
        {
            ctx.RequestServices.GetRequiredService<ILogger<PointerPlaybackFilter>>().LogInformation("Fixture projection refused: {Reason} ({Kind})", ex is Refused r ? r.Reason : RefusalCode.UnverifiedBinding, ex is Refused ? "refusal" : ex.GetType().Name);
            ctx.Response.Headers["X-Cleanyfin-Reason"]=(ex is Refused refused ? refused.Reason : RefusalCode.UnverifiedBinding).ToString();
            if (executed == null) action.Result = denied; else executed.Result = denied;
        }
    }

    // An ordinary local file the server would play directly; the layout itself is compared through the evidence label.
    private static bool DirectFile(MediaSourceInfo src) => src.Protocol == MediaProtocol.File && src.VideoType == MediaBrowser.Model.Entities.VideoType.VideoFile &&
        !src.IsRemote && !src.IsInfiniteStream && !src.RequiresOpening && src.SupportsDirectPlay && src.RunTimeTicks is > 0 &&
        src.MediaAttachments.Count == 0 && src.MediaStreams.All(s => !s.IsExternal && s.IsExternalUrl != true);
}
