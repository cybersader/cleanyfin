using System;
using System.Collections.Generic;
using Jellyfin.Plugin.Cleanyfin.Configuration;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
using MediaBrowser.Model.Serialization;

namespace Jellyfin.Plugin.Cleanyfin;

/// <summary>
/// Cleanyfin plugin: pulls community-tagged content-filter segments from the
/// cleanyfin API and exposes them to Jellyfin as native Media Segments. It ships
/// ONLY timestamps + type (metadata-only, decision R01) — never audio/video.
/// </summary>
public class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages
{
    public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
        : base(applicationPaths, xmlSerializer)
    {
        Instance = this;
    }

    /// <summary>Gets the current plugin instance.</summary>
    public static Plugin? Instance { get; private set; }

    /// <inheritdoc />
    public override string Name => "Cleanyfin";

    /// <inheritdoc />
    public override Guid Id => Guid.Parse("b1f7c2e0-4a3d-4c9a-9f2b-1e6d5a8c0f11");

    /// <inheritdoc />
    public override string Description =>
        "Crowdsourced content-filter segments for Jellyfin (metadata-only).";

    /// <inheritdoc />
    public IEnumerable<PluginPageInfo> GetPages()
    {
        return new[]
        {
            new PluginPageInfo
            {
                Name = Name,
                EmbeddedResourcePath = GetType().Namespace + ".Configuration.configPage.html",
            },
        };
    }
}
