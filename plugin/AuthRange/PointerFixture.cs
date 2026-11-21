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
    internal const string DirectoryPath = "/config/cleanyfin-lab/shelf";
    internal const string FilePath = DirectoryPath + "/Playback sample.strm";
    internal const long Ticks = 149_600_000; // immutable reviewed fixture; rechecked against plan at play
    internal static string Locator(PluginConfiguration c)
    {
        FixtureGate.Require(GrantTable<Binding>.IsHandle(c.LabPointerKey), RefusalCode.InvalidIdentifier);
        return FixtureGate.Origin(c).AbsoluteUri.TrimEnd('/') + "/Cleanyfin/pointer/" + c.LabPointerKey;
    }

    internal static bool Owned(Video v, PluginConfiguration c) => v.Path == FilePath && v.IsShortcut &&
        v.ShortcutPath == Locator(c) && new FileInfo(FilePath).Length is > 0 and < 2048 && File.ReadAllText(FilePath) == Locator(c) + "\n";

    internal static bool Matches(MediaSourceInfo s) => s.Path == FixtureGate.FixturePath && !s.IsRemote && s.RunTimeTicks == 200_000_000 &&
        s.MediaAttachments.Count == 0 && s.MediaStreams.Count == 2 && s.MediaStreams.All(x => !x.IsExternal && x.IsExternalUrl != true) &&
        s.MediaStreams.Any(x => x.Index == 0 && x.Type == MediaStreamType.Video && x.Codec == "h264") &&
        s.MediaStreams.Any(x => x.Index == 1 && x.Type == MediaStreamType.Audio && x.Codec == "aac");

    internal static MediaStream[] Streams(MediaSourceInfo s) => s.MediaStreams.Select(x => new MediaStream {
        Index=x.Index, Type=x.Type, Codec=x.Codec, Profile=x.Profile, Level=x.Level, Width=x.Width, Height=x.Height,
        AverageFrameRate=x.AverageFrameRate, RealFrameRate=x.RealFrameRate, BitRate=x.BitRate,
        SampleRate=x.SampleRate, Channels=x.Channels, ChannelLayout=x.ChannelLayout, IsDefault=x.IsDefault
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

    // Administrative creation is distinct from playback. No supplied paths, no overwrite or scan.
    [Authorize(Policy="RequiresElevation"), HttpPost("Cleanyfin/lab/create-pointer")]
    public IActionResult Create()
    {
        if (Plugin.Instance?.Configuration is not { EnableAuthRangeLab: true } config || !GrantTable<Binding>.IsHandle(config.LabPointerKey)) return NotFound();
        var contents = PointerFixture.Locator(config) + "\n";
        Directory.CreateDirectory(PointerFixture.DirectoryPath);
        if (System.IO.File.Exists(PointerFixture.FilePath)) return Conflict();
        var tmp = Path.Combine(PointerFixture.DirectoryPath, Guid.NewGuid().ToString("N") + ".tmp");
        try { System.IO.File.WriteAllText(tmp, contents); System.IO.File.Move(tmp, PointerFixture.FilePath); }
        finally { System.IO.File.Delete(tmp); }
        return NoContent();
    }
}

// Runs after shortcut parsing on this pin. No scanning, file probes or updates to other items.
public sealed class PointerMetadata(ILibraryManager library, IMediaStreamRepository streams, ILogger<PointerMetadata> log) : ICustomMetadataProvider<Video>, IHasOrder
{
    public string Name => "Cleanyfin fixture facts";
    public int Order => int.MaxValue;
    public Task<ItemUpdateType> FetchAsync(Video item, MetadataRefreshOptions options, CancellationToken ct)
    {
        if (item.Path != PointerFixture.FilePath || Plugin.Instance?.Configuration is not { EnableAuthRangeLab:true } c ||
            !PointerFixture.Owned(item,c) || !Guid.TryParseExact(c.LabItemId,"N",out var original) || original == item.Id ||
            library.GetItemById(original) is not Video v || v.GetMediaSources(false) is not [var s] || !PointerFixture.Matches(s))
            return Task.FromResult(ItemUpdateType.None);
        var facts = PointerFixture.Streams(s);
        var stored = streams.GetMediaStreams(new MediaStreamQuery { ItemId=item.Id });
        var changed = !JsonSerializer.SerializeToUtf8Bytes(stored).SequenceEqual(JsonSerializer.SerializeToUtf8Bytes(facts));
        if (changed) streams.SaveMediaStreams(item.Id, facts, ct);
        changed |= item.RunTimeTicks != PointerFixture.Ticks;
        item.RunTimeTicks = PointerFixture.Ticks;
        log.LogInformation("Fixture pointer metadata checked");
        return Task.FromResult(changed ? ItemUpdateType.MetadataImport : ItemUpdateType.None);
    }
}
