using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.Cleanyfin.Configuration;

/// <summary>Cleanyfin plugin settings.</summary>
public class PluginConfiguration : BasePluginConfiguration
{
    /// <summary>Base URL of the cleanyfin API server (the crowdsourced hub).</summary>
    public string ApiBaseUrl { get; set; } = "http://localhost:8080";

    /// <summary>Optional pseudonymous submitter id used for any future writes.</summary>
    public string SubmitterId { get; set; } = string.Empty;
}
