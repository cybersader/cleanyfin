# Spike B — The Segment Create/Delete Write API

> Feasibility spike for the cleanyfin marking PWA write path. Researched 2026-07-21 against current Jellyfin `master` and the Intro Skipper `10.11` branch (source-read, not just docs). Confidence tags: 🟢 verified from source/OpenAPI · 🟡 inferred, needs runtime test · 🔴 unverified/assumption.

## TL;DR

**There is no create/delete media-segment endpoint in core Jellyfin — not in 10.10, not in 10.11.** Core (`Jellyfin.Api/Controllers/MediaSegmentsController.cs`) exposes exactly one route, `GET /MediaSegments/{itemId}`, and nothing else. Writing segments over HTTP is 100% plugin territory.

The write surface everyone points at (`POST`/`DELETE /MediaSegmentsApi/{...}`) originated in **endrl/intro-skipper's `jellyfin-plugin-ms-api`** (a thin, generic wrapper over the core `IMediaSegmentManager`) and, as of the 10.11 release, was **folded into the Intro Skipper plugin** as `SegmentEditorController`. But the folded-in version is **not** the same neutral CRUD — it was rewritten to be tightly coupled to Intro Skipper's own analysis database and its 5 fixed analysis modes (Intro/Recap/Preview/Outro/Commercial). Its `DELETE` now requires an `itemId` + a `type` query param that must map to one of those modes (anything else throws), its `POST` silently no-ops unless Intro Skipper is an enabled provider on that item, and it returns an empty body (you don't get the new segment's `Id` back).

**Recommendation: cleanyfin should ship its OWN controller, copying the *original* `jellyfin-plugin-ms-api` pattern (a ~90-line thin wrapper over the core `IMediaSegmentManager.CreateSegmentAsync` / `DeleteSegmentAsync`), attributing writes to cleanyfin's own provider id.** Do NOT depend on Intro Skipper's `/MediaSegmentsApi` route: it forces an Intro-Skipper install, writes only under Intro Skipper's provider, restricts delete to its 5 modes, and its DELETE signature already changed once between plugin versions.

Also — and this is the more important architectural point — for cleanyfin's real design the marking PWA should POST to **cleanyfin's own Go crowdsource API** (the source of truth), and cleanyfin's `IMediaSegmentProvider` emits those into Jellyfin at library scan/refresh. A write-to-Jellyfin HTTP endpoint is only needed for *live* insertion without a rescan — and cleanyfin's own plugin can host that endpoint itself.

**Correction to prior grounding:** the wire DTO (`MediaSegmentDto`) has **no `StreamIndex`, no `Action`, and no `Comment`** field. Only `Id`, `ItemId`, `Type`, `StartTicks`, `EndTicks`. The `jellyfin-integration-mechanics.md` note ("ItemId + StreamIndex + Type + StartTicks/EndTicks + Action") describes the original design *proposal*, not the shipped API model.

---

## Findings (with sources)

### 1. Core Jellyfin exposes ONLY `GET /MediaSegments/{itemId}` — no write endpoint in 10.10 or 10.11. 🟢

`Jellyfin.Api/Controllers/MediaSegmentsController.cs` on `master` contains a single action:

```csharp
[Authorize]
[Tags("MediaSegment")]
public class MediaSegmentsController : BaseJellyfinApiController
{
    [HttpGet("{itemId}")]
    public async Task<ActionResult<QueryResult<MediaSegmentDto>>> GetItemSegments(
        [FromRoute, Required] Guid itemId,
        [FromQuery] IEnumerable<MediaSegmentType>? includeSegmentTypes = null) { ... }
}
```

- Route: `GET /MediaSegments/{itemId}` (optional `?includeSegmentTypes=Intro&includeSegmentTypes=Outro`).
- Auth: `[Authorize]` — **any authenticated user** (user token is enough for read).
- No `[HttpPost]`, no `[HttpDelete]`. Writes are done server-side by providers at scan time, or by a plugin exposing its own controller.

Source: `https://raw.githubusercontent.com/jellyfin/jellyfin/master/Jellyfin.Api/Controllers/MediaSegmentsController.cs`

### 2. The write DTO (`MediaSegmentDto`) is minimal: Id, ItemId, Type, StartTicks, EndTicks. No StreamIndex/Action/Comment. 🟢

```csharp
public class MediaSegmentDto
{
    public Guid Id { get; set; }
    public Guid ItemId { get; set; }
    [DefaultValue(MediaSegmentType.Unknown)]
    public MediaSegmentType Type { get; set; }
    public long StartTicks { get; set; }
    public long EndTicks { get; set; }
}
```

- **`StartTicks` / `EndTicks` are `long`, in .NET `DateTime`/`TimeSpan` ticks = 100-nanosecond units; 10,000,000 ticks = 1 second.** Confirmed by the plugin code doing `TimeSpan.FromTicks(segment.StartTicks).TotalSeconds`.
- `Action` does **not** exist on the segment. Skip / Ask-to-skip / Do-nothing is chosen per-client in playback settings, not stored on the segment. (Reinforces Spike-A: no per-segment mute flag to write.)
- `StreamIndex` does **not** exist on the shipped DTO (segments are item-scoped, not stream-scoped, in the final model).
- No free-text `Comment` field — cleanyfin category/metadata must live in cleanyfin's own DB, keyed by the segment, not inside Jellyfin.

Source: `https://raw.githubusercontent.com/jellyfin/jellyfin/master/MediaBrowser.Model/MediaSegments/MediaSegmentDto.cs`

### 3. `MediaSegmentType` enum: Unknown=0, Commercial=1, Preview=2, Recap=3, Outro=4, Intro=5. 🟢

Fixed 6-value enum, no content categories (confirms Spike-A). `Unknown` is the documented "default or custom" bucket. When cleanyfin writes segments it must pick one of these 6; `Unknown` or `Commercial` are the only semantically-neutral options for profanity/violence/nudity spans.

Source: `https://raw.githubusercontent.com/jellyfin/jellyfin/master/src/Jellyfin.Database/Jellyfin.Database.Implementations/Enums/MediaSegmentType.cs`

### 4. The core write plumbing IS present and injectable: `IMediaSegmentManager`. 🟢

`MediaBrowser.Controller.MediaSegments.IMediaSegmentManager` (a Controller-layer service any plugin can constructor-inject) exposes the actual write methods:

```csharp
Task<MediaSegmentDto> CreateSegmentAsync(MediaSegmentDto mediaSegment, string segmentProviderId);
Task DeleteSegmentAsync(Guid segmentId);
Task DeleteSegmentsAsync(Guid itemId, CancellationToken cancellationToken);
Task<IEnumerable<MediaSegmentDto>> GetSegmentsAsync(BaseItem item, IEnumerable<MediaSegmentType>? typeFilter, LibraryOptions libraryOptions, bool filterByProvider = true);
IEnumerable<(string Name, string Id)> GetSupportedProviders(BaseItem item);
Task RunSegmentPluginProviders(BaseItem baseItem, LibraryOptions libraryOptions, bool forceOverwrite, CancellationToken cancellationToken);
```

- `CreateSegmentAsync(dto, providerId)` returns the created `MediaSegmentDto` **with its generated `Id`** — so a plugin controller can return the Id to the caller for later deletion.
- Every segment is attributed to a `segmentProviderId`; a segment is only surfaced to a client if that provider is enabled for the item's library (`filterByProvider`). This is why "which provider owns the write" matters.

Source: `https://raw.githubusercontent.com/jellyfin/jellyfin/master/MediaBrowser.Controller/MediaSegments/IMediaSegmentManager.cs`

### 5. Reference impl #1 (the one to copy): original `jellyfin-plugin-ms-api` — a thin, generic wrapper. 🟢

`endrl/jellyfin-plugin-ms-api` and its fork `intro-skipper/jellyfin-plugin-ms-api` (both "Jellyfin 10.10", now marked **obsolete**) ship one controller, `MediaSegmentsApiController`:

```csharp
[Authorize(Policy = "RequiresElevation")]   // admin token required
[ApiController]
[Produces("application/json")]
[Route("MediaSegmentsApi")]
public class MediaSegmentsApiController : ControllerBase
{
    // GET /MediaSegmentsApi                         -> { version }
    // POST /MediaSegmentsApi/{itemId}?providerId=.. body: MediaSegmentDto
    [HttpPost("{itemId}")]
    public async Task<ActionResult<QueryResult<MediaSegmentDto>>> CreateSegmentAsync(
        [FromRoute, Required] Guid itemId,
        [FromQuery, Required] string providerId,
        [FromBody, Required] MediaSegmentDto segment)
    {
        var item = _libraryManager.GetItemById<BaseItem>(itemId);
        if (item is null) return NotFound();
        segment.ItemId = item.Id;
        var seg = await _mediaSegmentManager.CreateSegmentAsync(segment, providerId);
        return Ok(seg);
    }

    // DELETE /MediaSegmentsApi/{segmentId}
    [HttpDelete("{segmentId}")]
    public async Task DeleteSegmentAsync([FromRoute, Required] Guid segmentId)
        => await _mediaSegmentManager.DeleteSegmentAsync(segmentId);
}
```

This is the clean reference for cleanyfin: generic, provider-agnostic, returns the created segment (with Id), delete by segment Id only. It is nothing more than an HTTP skin over `IMediaSegmentManager`.

Source: `https://raw.githubusercontent.com/endrl/jellyfin-plugin-ms-api/master/Jellyfin.Plugin.MediaSegmentsApi/Controllers/MediaSegmentsApiController.cs` · repo READMEs confirm "now obsolete as the functionality is included in the 10.11 Jellyfin release of the Intro Skipper plugin."

### 6. Reference impl #2 (the CURRENT one, but coupled — do NOT depend on it): Intro Skipper `SegmentEditorController`. 🟢

In the Intro Skipper `10.11` branch, `IntroSkipper/Controllers/SegmentEditorController.cs` re-hosts the same base route but rewritten to be Intro-Skipper-specific:

- **Same route + auth:** `[Route("MediaSegmentsApi")]`, `[Authorize(Policy = Policies.RequiresElevation)]` (admin).
- **`POST /MediaSegmentsApi/{itemId}?providerId={providerId}`**, body `MediaSegmentDto`. But it now:
  - maps `segment.Type` → Intro Skipper `AnalysisMode` via `Plugin.MapSegmentTypeToMode`,
  - writes into **Intro Skipper's own plugin DB** (`Plugin.Instance.UpdateTimestampAsync(seg, mode, isUserProvided: true)`) in addition to Jellyfin,
  - then calls `CreateOrReplaceSegmentAsync`, which **looks up the provider on the item matching `Plugin.Instance.Name` (Intro Skipper) and, if Intro Skipper is not an enabled provider on that item, logs "provider not found" and returns without creating anything** (the `providerId` query param is effectively ignored — writes are forced under Intro Skipper's provider),
  - **returns empty `Ok()`** — you do NOT get the new segment `Id` back.
- **`DELETE /MediaSegmentsApi/{segmentId}?itemId={itemId}&type={type}`** — the signature CHANGED vs. the old plugin (which took `{segmentId}` only). `type` must be `intro`/`recap`/`preview`/`outro`/`credits`/`commercial`; **anything else throws `ArgumentOutOfRangeException`** (i.e. a 500). Non-commercial types are replace-one-per-type; commercial is dedup-by-ticks.

Net: the folded-in endpoint is a private extension of Intro Skipper, not a stable generic segment API. cleanyfin segments (which would be `Unknown` or `Commercial`) don't round-trip cleanly through it, and it drags in a hard Intro Skipper dependency.

Sources: `https://raw.githubusercontent.com/intro-skipper/intro-skipper/10.11/IntroSkipper/Controllers/SegmentEditorController.cs` · `https://raw.githubusercontent.com/intro-skipper/intro-skipper/10.11/IntroSkipper/Manager/MediaSegmentEditorService.cs`

### 7. Plugin identity / versions (for the manifest, if you ever do depend on it). 🟢

From `https://raw.githubusercontent.com/intro-skipper/manifest/refs/heads/main/10.11/manifest.json`:

- **Intro Skipper** — guid `c83d86bb-a1e0-4c35-a113-e2101cf4ee6b`, latest `1.10.11.22`, `targetAbi 10.11.11.0` (i.e. requires Jellyfin **10.11.11+**). The `SegmentEditorController` write API lives inside this plugin.
- There is ALSO a standalone **"Segment Editor"** plugin — guid `ace21d44-a4e5-4a85-ae75-acd2e24a9574`, latest `1.0.57.0`, `targetAbi 10.11.5.0` (this is endrl's `segment-editor` web-UI front-end that talks to the `MediaSegmentsApi` route; a reference for the *marking UI*, not the write plumbing).
- Manifest repo also lists EDL Creator, Chapter Creator, SkipMe.db (relevant to Spike-A / EDL export, not this spike).

Manifest distribution URL: `https://manifest.intro-skipper.org/manifest.json` (308-redirects; canonical raw is the GitHub `intro-skipper/manifest` repo, `10.11/manifest.json`).

### 8. Auth model. 🟢

Both plugin controllers require `RequiresElevation` = **administrator token**. Core GET requires only `[Authorize]` (any user). Jellyfin tokens are passed via the `Authorization: MediaBrowser Token="<token>"` header (or legacy `X-Emby-Token` / `?api_key=`). Implication for the marking PWA: **writing segments directly into Jellyfin requires an admin token** — a normal viewer's token cannot write. This is a strong reason to route contributor submissions through cleanyfin's own API (pseudonymous, no Jellyfin admin creds in the PWA) rather than writing straight to Jellyfin. 🟡 (token-header behavior is standard Jellyfin; not re-verified at runtime this spike.)

---

## Recommendation for cleanyfin

**R-B1 — Ship cleanyfin's OWN write controller; copy the *original* `jellyfin-plugin-ms-api` (Finding 5), not Intro Skipper's folded version.** ~90 lines: inject `IMediaSegmentManager` + `ILibraryManager`, expose `POST /Cleanyfin/Segments/{itemId}` and `DELETE /Cleanyfin/Segments/{segmentId}`, attribute writes to cleanyfin's own `segmentProviderId`, return the created DTO (with Id). This keeps writes provider-consistent with cleanyfin's `IMediaSegmentProvider`, avoids a hard Intro Skipper dependency, and gives clean create→delete round-tripping regardless of segment `Type`. Use cleanyfin's own route namespace, not `/MediaSegmentsApi`, to avoid colliding with Intro Skipper if both are installed.

**R-B2 — Make the crowdsource Go API the write target for the PWA, not Jellyfin.** The marking PWA (pseudonymous contributors, no accounts, no admin token) POSTs in/out points + rich category to cleanyfin's Go API. cleanyfin's `IMediaSegmentProvider` materializes those into Jellyfin at scan/refresh. The plugin's own controller (R-B1) is only for *live* insert-without-rescan (optionally triggering a targeted `RunSegmentPluginProviders` refresh for the item). This preserves "no forced accounts / no admin creds in the client" and keeps the rich taxonomy in cleanyfin's DB (Jellyfin only stores the 6-type enum + ticks).

**R-B3 — Do not treat `/MediaSegmentsApi` as a stable contract.** It is third-party (Intro Skipper), young (10.10+), and its DELETE signature already changed once (segmentId-only → segmentId+itemId+type). If you must interoperate with the existing Segment Editor ecosystem, wrap it behind an adapter and pin Intro Skipper `>= 1.10.11.x` / Jellyfin `10.11.11+`.

## Trustworthiness / stability of the seam

- **`IMediaSegmentManager` + `MediaSegmentDto` (core Controller/Model types):** 🟡→🟢 medium-high. This is the same surface every segment provider uses; it's the intended plugin extension point (author: JPVenson, core). Stable enough to build on, but young (shipped 10.10, Nov 2024) so treat minor field/signature drift across major Jellyfin versions as possible. cleanyfin already takes this dependency by being an `IMediaSegmentProvider`, so no *new* risk.
- **The `/MediaSegmentsApi` HTTP route:** 🔴 low. Not core, not a documented contract, owned by a third-party plugin, and already changed shape once. Avoid depending on it.
- **Auth (`RequiresElevation` for writes):** 🟢 stable and unlikely to change.

## Open / needs runtime verification

- 🟡 **Does `IMediaSegmentManager.CreateSegmentAsync` require the target `providerId` to correspond to an *enabled* provider on the item's library for the segment to be returned by `GET /MediaSegments/{itemId}`?** The Intro Skipper editor service explicitly checks `GetSupportedProviders(item)` before writing, implying yes for *visibility*. Needs a runtime test: write under cleanyfin's providerId, then GET, confirm it comes back for a client. If provider-must-be-enabled holds, cleanyfin's provider must be registered+enabled on the library for live writes to surface.
- 🟡 **Live-write vs. rescan:** confirm whether a segment created via `CreateSegmentAsync` is immediately visible to a playing client, or only after the item is refreshed. Determines whether R-B2's "live insert" path is real or whether a targeted `RunSegmentPluginProviders` refresh is required.
- 🟡 **Token/auth at runtime:** confirm the exact header form the PWA/admin tooling must send (`Authorization: MediaBrowser Token=...`) and that a non-admin user token is indeed rejected by `RequiresElevation` on the write route.
- 🟡 **Does core 10.11 stable actually match `master`?** This spike read `master` + the Intro Skipper `10.11` branch. Verify against a pinned Jellyfin `10.11.x` release tag that `MediaSegmentsController` still has GET-only and `MediaSegmentDto` still has the 5 fields (no late-added `Action`/`StreamIndex`).
- 🔴 **OpenAPI cross-check:** pull `api.jellyfin.org` / the server's `/api-docs/openapi.json` for a pinned 10.11.x to confirm no core write route exists there either (source says no; OpenAPI is the belt-and-suspenders check).

## Sources

- Core GET-only controller: <https://github.com/jellyfin/jellyfin/blob/master/Jellyfin.Api/Controllers/MediaSegmentsController.cs>
- Core `MediaSegmentDto`: <https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/MediaSegments/MediaSegmentDto.cs>
- Core `IMediaSegmentManager`: <https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Controller/MediaSegments/IMediaSegmentManager.cs>
- Core `MediaSegmentType` enum: <https://github.com/jellyfin/jellyfin/blob/master/src/Jellyfin.Database/Jellyfin.Database.Implementations/Enums/MediaSegmentType.cs>
- Original ms-api controller (reference to copy): <https://github.com/endrl/jellyfin-plugin-ms-api/blob/master/Jellyfin.Plugin.MediaSegmentsApi/Controllers/MediaSegmentsApiController.cs>
- ms-api "now obsolete / folded into Intro Skipper 10.11": <https://github.com/intro-skipper/jellyfin-plugin-ms-api>
- Current Intro Skipper `SegmentEditorController` (coupled write API): <https://github.com/intro-skipper/intro-skipper/blob/10.11/IntroSkipper/Controllers/SegmentEditorController.cs>
- Intro Skipper `MediaSegmentEditorService` (provider-coupling logic): <https://github.com/intro-skipper/intro-skipper/blob/10.11/IntroSkipper/Manager/MediaSegmentEditorService.cs>
- Intro Skipper manifest (guids/versions/targetAbi): <https://raw.githubusercontent.com/intro-skipper/manifest/refs/heads/main/10.11/manifest.json>
- Media segments provider PRs (core feature origin): <https://github.com/jellyfin/jellyfin/pull/12345> · <https://github.com/jellyfin/jellyfin/pull/12359>
