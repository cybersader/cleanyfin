using System;
using System.Net.Http;
using System.Net.Http.Json;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Common.Net;
using MediaBrowser.Controller.Library;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;

namespace Jellyfin.Plugin.Cleanyfin;

/// <summary>
/// The write path (decision R14, live-insert): accepts a segment marked from the
/// PWA and forwards it to the cleanyfin API keyed on the SAME release fingerprint
/// the provider later queries (R04). The browser can't read file bytes, so the
/// plugin (server-side, has the path) resolves the moviehash on its behalf.
/// </summary>
[ApiController]
[Authorize]
[Route("Cleanyfin")]
public class SegmentsController : ControllerBase
{
    private readonly ILibraryManager _libraryManager;
    private readonly IHttpClientFactory _httpClientFactory;

    public SegmentsController(
        ILibraryManager libraryManager,
        IHttpClientFactory httpClientFactory)
    {
        _libraryManager = libraryManager;
        _httpClientFactory = httpClientFactory;
    }

    /// <summary>
    /// POST /Cleanyfin/Segments -> resolves the item's fingerprint and proxies the
    /// mark to {ApiBaseUrl}/api/v1/segments, returning the cleanyfin API's status
    /// and body verbatim.
    /// </summary>
    [HttpPost("Segments")]
    [ProducesResponseType(StatusCodes.Status200OK)]
    [ProducesResponseType(StatusCodes.Status404NotFound)]
    [ProducesResponseType(StatusCodes.Status502BadGateway)]
    public async Task<IActionResult> PostSegment(
        [FromBody] SubmitSegmentRequest request,
        CancellationToken cancellationToken)
    {
        var item = _libraryManager.GetItemById(request.ItemId);
        if (item is null)
        {
            return NotFound();
        }

        var fingerprint = Moviehash.Fingerprint(item.Path, request.ItemId);
        var durationMs = item.RunTimeTicks.HasValue
            ? item.RunTimeTicks.Value / TimeSpan.TicksPerMillisecond
            : 0L;

        var apiBase = (Plugin.Instance?.Configuration.ApiBaseUrl ?? "http://localhost:8080").TrimEnd('/');
        var url = $"{apiBase}/api/v1/segments";

        var payload = new SubmitSegmentPayload
        {
            Fingerprint = fingerprint,
            DurationMs = durationMs,
            StartMs = request.StartMs,
            EndMs = request.EndMs,
            Category = request.Category,
            Severity = request.Severity,
            Action = request.Action,
            SubmitterId = request.SubmitterId,
        };

        try
        {
            var client = _httpClientFactory.CreateClient(NamedClient.Default);
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            cts.CancelAfter(TimeSpan.FromSeconds(10));

            using var resp = await client.PostAsJsonAsync(url, payload, cts.Token).ConfigureAwait(false);
            var body = await resp.Content.ReadAsStringAsync(cts.Token).ConfigureAwait(false);
            var contentType = resp.Content.Headers.ContentType?.MediaType ?? "application/json";

            // Proxy the cleanyfin API's status + body back to the caller verbatim.
            return new ContentResult
            {
                StatusCode = (int)resp.StatusCode,
                Content = body,
                ContentType = contentType,
            };
        }
        catch (Exception ex)
        {
            // Never throw unhandled — the upstream may be down, slow, or timed out.
            return StatusCode(
                StatusCodes.Status502BadGateway,
                new { error = "cleanyfin: failed to forward segment to the API", detail = ex.Message });
        }
    }
}

/// <summary>Body of a segment mark submitted from the PWA (decision R14).</summary>
public class SubmitSegmentRequest
{
    public Guid ItemId { get; set; }

    public long StartMs { get; set; }

    public long EndMs { get; set; }

    public string Category { get; set; } = string.Empty;

    public int Severity { get; set; }

    public string Action { get; set; } = string.Empty;

    public string SubmitterId { get; set; } = string.Empty;
}

/// <summary>What the plugin forwards to the cleanyfin API (fingerprint-keyed).</summary>
public class SubmitSegmentPayload
{
    public string Fingerprint { get; set; } = string.Empty;

    public long DurationMs { get; set; }

    public long StartMs { get; set; }

    public long EndMs { get; set; }

    public string Category { get; set; } = string.Empty;

    public int Severity { get; set; }

    public string Action { get; set; } = string.Empty;

    public string SubmitterId { get; set; } = string.Empty;
}
