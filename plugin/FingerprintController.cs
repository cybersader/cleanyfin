using System;
using MediaBrowser.Controller.Library;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;

namespace Jellyfin.Plugin.Cleanyfin;

/// <summary>
/// Exposes the release fingerprint of a library item to the marking PWA. The
/// browser can't read file bytes, so the plugin (server-side, has the path)
/// resolves the moviehash on its behalf so the PWA submits under the SAME
/// fingerprint the provider later queries (decision R04).
/// </summary>
[ApiController]
[Authorize]
[Route("Cleanyfin")]
public class FingerprintController : ControllerBase
{
    private readonly ILibraryManager _libraryManager;

    public FingerprintController(ILibraryManager libraryManager)
    {
        _libraryManager = libraryManager;
    }

    /// <summary>GET /Cleanyfin/Fingerprint?itemId=... -> the item's fingerprint.</summary>
    [HttpGet("Fingerprint")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    public ActionResult<FingerprintResult> GetFingerprint([FromQuery] Guid itemId)
    {
        var item = _libraryManager.GetItemById(itemId);
        if (item is null)
        {
            return NotFound();
        }

        return new FingerprintResult
        {
            ItemId = itemId,
            Fingerprint = Moviehash.Fingerprint(item.Path, itemId),
        };
    }
}

/// <summary>Fingerprint lookup response.</summary>
public class FingerprintResult
{
    public Guid ItemId { get; set; }

    public string Fingerprint { get; set; } = string.Empty;
}
