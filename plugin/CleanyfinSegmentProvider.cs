using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using Jellyfin.Database.Implementations.Enums;
using MediaBrowser.Common.Net;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Controller.MediaSegments;
using MediaBrowser.Model;
using MediaBrowser.Model.MediaSegments;
using Microsoft.Extensions.Logging;

namespace Jellyfin.Plugin.Cleanyfin;

/// <summary>
/// A Media Segment provider that fetches community-tagged segments from the
/// cleanyfin API instead of analyzing media locally (the Intro Skipper pattern,
/// decision R02). Output is global per item — Jellyfin has no per-user segment
/// context (Spike A / R13), so per-profile enforcement is NOT done here.
/// </summary>
public class CleanyfinSegmentProvider : IMediaSegmentProvider
{
    private readonly IHttpClientFactory _httpClientFactory;
    private readonly ILibraryManager _libraryManager;
    private readonly ILogger<CleanyfinSegmentProvider> _logger;

    public CleanyfinSegmentProvider(
        IHttpClientFactory httpClientFactory,
        ILibraryManager libraryManager,
        ILogger<CleanyfinSegmentProvider> logger)
    {
        _httpClientFactory = httpClientFactory;
        _libraryManager = libraryManager;
        _logger = logger;
    }

    /// <inheritdoc />
    public string Name => "Cleanyfin";

    /// <inheritdoc />
    public ValueTask<bool> Supports(BaseItem item) => ValueTask.FromResult(item is Video);

    /// <inheritdoc />
    public async Task<IReadOnlyList<MediaSegmentDto>> GetMediaSegments(
        MediaSegmentGenerationRequest request,
        CancellationToken cancellationToken)
    {
        var apiBase = (Plugin.Instance?.Configuration.ApiBaseUrl ?? "http://localhost:8080").TrimEnd('/');

        // Release fingerprint = moviehash of the file, "jf:" + ItemId fallback
        // (decision R04). Same scheme the PWA resolves via /Cleanyfin/Fingerprint,
        // so a mark submitted from the PWA lines up with what we query here.
        var item = _libraryManager.GetItemById(request.ItemId);
        var fp = Moviehash.Fingerprint(item?.Path, request.ItemId);
        var url = $"{apiBase}/api/v1/segments?fp={Uri.EscapeDataString(fp)}";

        try
        {
            var client = _httpClientFactory.CreateClient(NamedClient.Default);
            using var cts = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            cts.CancelAfter(TimeSpan.FromSeconds(10));

            var resp = await client.GetFromJsonAsync<SegmentsResponse>(url, cts.Token).ConfigureAwait(false);
            var result = new List<MediaSegmentDto>();
            if (resp?.Segments is not null)
            {
                foreach (var s in resp.Segments)
                {
                    result.Add(new MediaSegmentDto
                    {
                        ItemId = request.ItemId,
                        // No content-filter segment type exists; overload Unknown
                        // and carry the real category in cleanyfin's own DB (R14).
                        Type = MediaSegmentType.Unknown,
                        StartTicks = s.StartMs * TimeSpan.TicksPerMillisecond,
                        EndTicks = s.EndMs * TimeSpan.TicksPerMillisecond,
                    });
                }
            }

            return result;
        }
        catch (Exception ex)
        {
            // Never throw into the scan — degrade to "no segments" on any failure.
            _logger.LogWarning(ex, "cleanyfin: failed to fetch segments for {ItemId}", request.ItemId);
            return Array.Empty<MediaSegmentDto>();
        }
    }

    private sealed class SegmentsResponse
    {
        [JsonPropertyName("segments")]
        public List<ApiSegment>? Segments { get; set; }
    }

    private sealed class ApiSegment
    {
        [JsonPropertyName("startMs")]
        public long StartMs { get; set; }

        [JsonPropertyName("endMs")]
        public long EndMs { get; set; }

        [JsonPropertyName("category")]
        public string? Category { get; set; }

        [JsonPropertyName("action")]
        public string? Action { get; set; }
    }
}
