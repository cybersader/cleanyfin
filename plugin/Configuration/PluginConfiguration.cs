using MediaBrowser.Model.Plugins;

namespace Jellyfin.Plugin.Cleanyfin.Configuration;

/// <summary>Cleanyfin plugin settings.</summary>
public class PluginConfiguration : BasePluginConfiguration
{
    /// <summary>Base URL of the cleanyfin API server (the crowdsourced hub).</summary>
    public string ApiBaseUrl { get; set; } = "http://localhost:8080";

    /// <summary>Optional pseudonymous submitter id used for any future writes.</summary>
    public string SubmitterId { get; set; } = string.Empty;

    // Disposable 10.11.11 auth-range fixture only. Not household playback configuration.
    public bool EnableAuthRangeLab { get; set; }
    // Mutually exclusive diagnostic mode; fixed-plan issuance is unavailable in shelf mode.
    public bool EnableLegacyRangeFixture { get; set; }
    public string LabOrigin { get; set; } = string.Empty;
    public string LabItemId { get; set; } = string.Empty;
    public string LabViewerId { get; set; } = string.Empty;
    public string LabDeviceId { get; set; } = string.Empty;
    public string LabSourceSha256 { get; set; } = string.Empty;
    public string LabPlanSha256 { get; set; } = string.Empty;
    // An explicit lab marker for the separately configured emulator origin.
    public string LabPointerKey { get; set; } = string.Empty;
    public bool LabWithholdProjection { get; set; }
    public int LabLifetimeSeconds { get; set; } = 120;
}
