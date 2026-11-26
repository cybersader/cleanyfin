using Microsoft.Extensions.DependencyInjection;
using System;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Cleanyfin.Delivery;
using Jellyfin.Plugin.Cleanyfin.Configuration;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.Persistence;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Model.Dto;
using MediaBrowser.Model.Entities;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.Logging;
using System.Text.Json;

namespace Jellyfin.Plugin.Cleanyfin.AuthRange;

internal static class PointerFixture
{
    internal static MediaStream[] Streams(MediaSourceInfo s) => s.MediaStreams.Select(x => new MediaStream
    {
        Index = x.Index,
        Type = x.Type,
        Codec = x.Codec,
        Profile = x.Profile,
        Level = x.Level,
        Width = x.Width,
        Height = x.Height,
        AverageFrameRate = x.AverageFrameRate,
        RealFrameRate = x.RealFrameRate,
        BitRate = x.BitRate,
        SampleRate = x.SampleRate,
        Channels = x.Channels,
        ChannelLayout = x.ChannelLayout,
        IsDefault = x.IsDefault
    }).ToArray();
}

[ApiController]
public sealed class FixturePointerController : ControllerBase
{
    [AllowAnonymous, HttpGet("Cleanyfin/pointer/{key}"), HttpHead("Cleanyfin/pointer/{key}")]
    public IActionResult Inert([FromServices] ILogger<FixturePointerController> log)
    {
        log.LogInformation("GET/HEAD /Cleanyfin/pointer/{{id}} 410");
        return StatusCode(410);
    }

    [Authorize(Policy = "RequiresElevation"), HttpGet("Cleanyfin/lab/status")]
    public IActionResult Status() => Ok(new { Issued = HttpContext.RequestServices.GetRequiredService<GrantService>().Table.Issued });

    [Authorize(Policy = "RequiresElevation"), HttpPost("Cleanyfin/lab/reconcile")]
    public async Task<IActionResult> Reconcile() => await Run(false);
    [Authorize(Policy = "RequiresElevation"), HttpPost("Cleanyfin/lab/cleanup")]
    public async Task<IActionResult> Cleanup() => await Run(true);
    private async Task<IActionResult> Run(bool cleanup)
    {
        if (cleanup == FixtureGate.PointerEnabled(Plugin.Instance?.Configuration)) return Conflict();
        return Ok(new { Count = await HttpContext.RequestServices.GetRequiredService<ShelfStore>().Reconcile(cleanup, HttpContext.RequestAborted) });
    }
}

public sealed class PointerMetadata(IServiceProvider services, IMediaStreamRepository streams, ILogger<PointerMetadata> log) : ICustomMetadataProvider<Video>, IHasOrder
{
    internal const string TestNotice = "Test filtering — skips may have a brief sound or picture seam. See the Cleanyfin review page for measurements.";
    public string Name => "Cleanyfin shelf facts";
    public int Order => int.MaxValue;
    public Task<ItemUpdateType> FetchAsync(Video item, MetadataRefreshOptions options, CancellationToken ct)
    {
        if (!FixtureGate.PointerEnabled(Plugin.Instance?.Configuration)) return Task.FromResult(ItemUpdateType.None);
        var store = services.GetRequiredService<ShelfStore>();
        if (!store.PointerPath(item.Path)) return Task.FromResult(ItemUpdateType.None);
        try
        {
            var row = store.Lookup(item); var input = store.Resolve(row.Viewer, row.Item);
            if (input.Generation != row.Generation || input.Resolution.TimelineKey != row.Timeline) return Task.FromResult(ItemUpdateType.None);
            var facts = PointerFixture.Streams(input.Source);
            var stored = streams.GetMediaStreams(new MediaStreamQuery { ItemId = item.Id });
            var changed = !JsonSerializer.SerializeToUtf8Bytes(stored).SequenceEqual(JsonSerializer.SerializeToUtf8Bytes(facts));
            if (changed) streams.SaveMediaStreams(item.Id, facts, ct);
            // Metadata is shared by clients: conservatively label the shelf item before granting any test offer.
            var testLabel = store.TestLabel(input, row.Viewer);
            if (testLabel && item.Overview != TestNotice) { item.Overview = TestNotice; changed = true; }
            else if (!testLabel && item.Overview == TestNotice) { item.Overview = null; changed = true; }
            changed |= item.RunTimeTicks != row.Ticks;
            item.RunTimeTicks = row.Ticks;
            log.LogInformation("Fixture pointer metadata checked");
            return Task.FromResult(changed ? ItemUpdateType.MetadataImport : ItemUpdateType.None);
        }
        catch (Exception) { return Task.FromResult(ItemUpdateType.None); }
    }
}
