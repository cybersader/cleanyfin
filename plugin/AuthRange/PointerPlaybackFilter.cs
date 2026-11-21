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
            if (c?.EnableAuthRangeLab!=true || c.LabWithholdProjection || ctx.User.Identity?.IsAuthenticated != true || ctx.Request.Query.Count != 0 ||
                cad.MethodInfo.Name != "GetPostedPlaybackInfo" || !action.ActionArguments.TryGetValue("playbackInfoDto", out var dto) ||
                dto?.GetType().FullName != "Jellyfin.Api.Models.MediaInfoDtos.PlaybackInfoDto") { action.Result = denied; return; }
            // The bound DTO resides in Jellyfin.Api (not its plugin SDK). Missing pinned members refuse.
            object? Field(string name) => (dto.GetType().GetProperty(name) ?? throw new MissingMemberException(name)).GetValue(dto);
            if (Field("DeviceProfile") is not DeviceProfile { Name:"mpv" } profile ||
                Field("MediaSourceId") is string selected && (!Guid.TryParse(selected,out var selectedId) || selectedId != id) ||
                Field("LiveStreamId") != null || Field("UserId") != null || Field("EnableDirectPlay") is false ||
                Field("AudioStreamIndex") is not (null or 1) || Field("SubtitleStreamIndex") is not (null or -1) ||
                profile.ContainerProfiles.Length != 0 || profile.CodecProfiles.Length != 0 ||
                !profile.DirectPlayProfiles.Any(p => p.Type == DlnaProfileType.Video && p.Container.Split(',').Contains("mp4") &&
                    p.VideoCodec?.Split(',').Contains("h264") == true && p.AudioCodec?.Split(',').Contains("aac") == true))
            { action.Result = denied; return; }
            var gate = ctx.RequestServices.GetRequiredService<FixtureGate>();
            var (binding,ticks) = await gate.Prepare(ctx,id); // both ACLs and exactly one immutable local original
            // Run the real POST for the original, with this principal/profile/bitrate/track choice.
            // Its result never leaves MVC: replace it BEFORE inspecting or issuing a capability.
            action.ActionArguments["itemId"] = Guid.ParseExact(binding.Item,"N");
            action.ActionArguments["mediaSourceId"] = binding.Source;
            action.ActionArguments["autoOpenLiveStream"] = false;
            executed = await next();
            var native = executed.Result;
            executed.Result = denied;
            if (executed.Exception != null || native is not ObjectResult { Value: PlaybackInfoResponse { ErrorCode:null, MediaSources:[var src] } info } ||
                !src.SupportsDirectPlay || src.TranscodingUrl != null || src.Id != binding.Source || (src.Path!=binding.FilePath || !PinnedAsset.Matches(src)) ||
                Field("MaxStreamingBitrate") is int max && !(src.Bitrate <= max) ||
                Field("MaxAudioChannels") is int channels && !(src.MediaStreams.Single(x=>x.Index==1).Channels <= channels)) return;
            gate.Validate(binding,ctx);
            var projected = new MediaSourceInfo { Id=id.ToString("N"), Protocol=MediaProtocol.Http, IsRemote=true,
                SupportsDirectPlay=true, SupportsDirectStream=false, SupportsTranscoding=false, Container="mp4", RunTimeTicks=ticks,
                Bitrate=src.Bitrate, DefaultAudioStreamIndex=1, MediaStreams=PointerFixture.Streams(src), Path=gate.Issue(binding) };
            ctx.Response.Headers.Remove("X-Cleanyfin-Reason");
            executed.Result = new OkObjectResult(new PlaybackInfoResponse { MediaSources=[projected], PlaySessionId=info.PlaySessionId });
        }
        catch (Exception ex) // only a bounded reason, never exception text or URL
        {
            ctx.RequestServices.GetRequiredService<ILogger<PointerPlaybackFilter>>().LogInformation("Fixture projection refused: {Reason}", ex is Refused r ? r.Reason : RefusalCode.UnverifiedBinding);
            ctx.Response.Headers["X-Cleanyfin-Reason"]=(ex is Refused refused ? refused.Reason : RefusalCode.UnverifiedBinding).ToString();
            if (executed == null) action.Result = denied; else executed.Result = denied;
        }
    }
}
