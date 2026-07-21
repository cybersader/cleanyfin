---
title: Spike A — Per-profile enforcement
description: Can Jellyfin enforce per-profile filtering server-side? Source-verified verdict + the chosen architecture.
sidebar:
  order: 7
---

*Feasibility spike from the 2026-07-21 research fan-out — source-verified against Jellyfin 10.11. Confidence tags and sources preserved.*

## TL;DR

**There is NO server-side mechanism in Jellyfin's Media Segments pipeline that carries per-user context, at any stage — verified from 10.11 source.** Segment *generation* (`IMediaSegmentProvider.GetMediaSegments`) receives a request object containing only `ItemId` + `ExistingSegments` — zero user/profile/session context, so a provider **cannot** vary its output per profile even in principle. Segment *serving* (`GET /MediaSegments/{itemId}`) applies **no** per-user filtering to the segments; the only user-aware step is a whole-item access check (`GetItemById(itemId, User.GetUserId())`) that enforces parental rating / library ACLs on the *entire item*, not on sub-item segments. Segment *actions* (Skip / Ask / None) are chosen **client-side** ("differs per client"), inconsistently stored (web = synced per-user `UserSettings`; Android TV = per-device local), so they are not a server-enforced per-profile ACL either.

The only genuinely per-user, server-enforced, PUBLIC/stable controls Jellyfin has are the **parental/tag/schedule item gates** — but they gate whole titles, which is the wrong granularity for sub-scene filtering. The session-event seam (`ISessionManager.PlaybackProgress`) *does* carry user context but is **observational**, cannot rewrite a segment mid-stream, and is **demonstrably fragile** — it broke across 10.10 → 10.11 (`get_Users()` removed, `IUserManager.GetUserById` signature changed, the `User` entity moved namespaces).

**Recommendation:** For v1, accept the user-blind native pipeline and be honest about the trust boundary (provider emits a global segment set; per-profile category *selection* is client-side opt-in). For households that need real, un-bypassable per-kid enforcement, offer an **optional** cleanyfin reverse-proxy/companion that maps the authenticated Jellyfin user → cleanyfin profile and filters the `/MediaSegments` **response** — this delivers true per-profile enforcement to *unmodified native clients*, stays metadata-only (it only drops/keeps timestamps), and depends **only on the stable public HTTP contract**, not on any fragile internal plugin hook. Do NOT build enforcement on the session-event seam.

---

## Findings (with sources)

### 1. Segment generation is user-blind by contract — the request object has no user field.  🟢 verified

`IMediaSegmentProvider.GetMediaSegments(MediaSegmentGenerationRequest request, CancellationToken ct)` returns `Task<IReadOnlyList<MediaSegmentDto>>`. The request record `MediaSegmentGenerationRequest` has **exactly two properties**:
- `ItemId` (Guid) — the item to extract segments from
- `ExistingSegments` (IReadOnlyList<MediaSegmentDto>) — segments this provider produced on an earlier scan

There is **no** `UserId`, `User`, `Session`, or profile field. A provider is invoked at scan/refresh time and produces **one set of segments per item, shared by every user**. This confirms the prior deep-dive: **a provider cannot vary output per profile at all** — not by design choice but by the shape of the API surface it is handed.

Sources: `MediaBrowser.Controller/MediaSegments/IMediaSegmentProvider.cs`, `MediaBrowser.Model/MediaSegments/MediaSegmentGenerationRequest.cs` (jellyfin `master`).

### 2. Segment serving applies whole-item user gating but NO per-user segment filtering.  🟢 verified

`MediaSegmentsController` exposes `GET /{itemId}` with params `itemId` and optional `includeSegmentTypes`. It is `[Authorize]` (any authenticated user). Internally it calls `_libraryManager.GetItemById<BaseItem>(itemId, User.GetUserId())` — so if the requesting user **cannot access the item** (parental rating, blocked library/tag), the lookup returns null and no segments come back. But once the item resolves, **the full global segment set is returned** — there is no per-user filtering of *which* segments a profile sees.

The manager method confirms this: `MediaSegmentManager.GetSegmentsAsync(BaseItem? item, IEnumerable<MediaSegmentType>? typeFilter, LibraryOptions libraryOptions, bool filterByProvider = true)` — **no user parameter anywhere** in the read path.

Net: the *only* per-user hook in the entire segment pipeline is a coarse whole-item access check. There is no sub-item, per-profile segment ACL.

Sources: `Jellyfin.Api/Controllers/MediaSegmentsController.cs`, `Jellyfin.Server.Implementations/MediaSegments/MediaSegmentManager.cs` (jellyfin `master`).

### 3. Segment ACTIONS are client-side and inconsistently stored — not a server per-profile policy.  🟢 verified

Official docs: *"Set actions for the different segment types, the way you do this differs per client, but they are generally found in the playback settings of the client."* On jellyfin-web the setting lives in `UserSettings` (synced to the server via the DisplayPreferences API, so it happens to be per-user for that client); on Android TV it is an AppSetting stored in local device storage (per install, shared by all profiles on that device). So the *enforcement decision* (skip vs not) is neither centralized nor uniformly per-profile. You cannot rely on it as an access control.

Sources: <https://jellyfin.org/docs/general/server/metadata/media-segments/> · jellyfin-web Settings Management (UserSettings vs AppSettings) via DeepWiki.

### 4. Session/playback events carry user context but are observational AND fragile.  🟢 verified

`ISessionManager` exposes events a plugin can subscribe to: `PlaybackStart` / `PlaybackProgress` (`EventHandler<PlaybackProgressEventArgs>`), `PlaybackStopped` (`PlaybackStopEventArgs`), plus `SessionStarted/Ended/Activity`. `PlaybackProgressEventArgs` carries `Session` (SessionInfo, which has UserId), `Users` (List<User>), `Item`, `MediaSourceId`, `PlaybackPositionTicks`, `IsPaused`, `PlaySessionId`, etc. So a plugin *can* know "user X is at tick T in item Y."

But this seam is **for reacting, not for editing the stream**: a plugin can `SendPlaystateCommand` (Stop/Pause) or `SendMessageCommand` (display a message) to the session, which is coarse — it can stop playback of a forbidden title, but **cannot skip/mute a segment span**. And it is **demonstrably version-fragile**: Jellyfin 10.11 removed `PlaybackProgressEventArgs.get_Users()` and changed `IUserManager.GetUserById(Guid)` (the `User` entity moved out of `Jellyfin.Data.Entities` into the new database-abstraction namespace), which broke PlaybackReporting and KodiSyncQueue with `MissingMethodException` at runtime. This is exactly the "internal implementation detail that breaks across versions" risk.

Sources: `MediaBrowser.Controller/Session/ISessionManager.cs`, `MediaBrowser.Controller/Library/PlaybackProgressEventArgs.cs` (jellyfin `master`); breakage: <https://github.com/jellyfin/jellyfin/issues/14893>.

### 5. Parental / tag / schedule gates are the ONLY robust per-user server enforcement — but whole-item only.  🟢 verified (well-documented feature)

Jellyfin's real per-user access model is the user policy: `MaxParentalRating`, allowed/blocked tags, `BlockedMediaFolders`/library access, and `AccessSchedules`. These are enforced server-side across the API (they are why the segment controller's item lookup can return null) and are **PUBLIC, documented, stable admin-facing features** — high trust. They gate **whole titles**, so they can hide a movie from a kid but cannot filter a scene inside a movie the kid *is* allowed to watch. Wrong granularity for cleanyfin's core value, but relevant as a complementary layer (e.g. block R-rated titles entirely, filter PG-13 ones by segment).

Sources: Jellyfin multi-user / parental-controls docs & guides (Dashboard → Users → policy); observed enforcement in `MediaSegmentsController` item lookup.

### 6. Plugins CAN expose their own user-aware API controllers — this is where "do it ourselves" lives.  🟢 verified

A plugin can add a `ControllerBase` under `Jellyfin.Api`, decorate it `[Authorize]` (optionally `[Authorize(Policy = Policies.RequiresElevation)]`), and read the authenticated `User` claims — Jellyfin's `CustomAuthenticationHandler` maps the token into a `ClaimsPrincipal` carrying `UserId`, `DeviceId`, `Token`. So **cleanyfin can host its own per-profile endpoint that IS user-aware** (unlike the core segment endpoint). The catch: **native Jellyfin clients will never call cleanyfin's endpoint for segments** — they call the core `GET /MediaSegments/{itemId}`. A cleanyfin user-aware endpoint therefore only helps a *cooperating* client/companion, or a proxy that sits in the client→server path.

Note: a plugin **cannot** override/replace the core `MediaSegmentsController`, so it cannot make the *native* segment endpoint per-user from inside the plugin.

Sources: jellyfin-plugin-template (custom controllers), PlaybackReporting `PlaybackReportingActivityController` (`[Authorize(Policy=...)]`), Jellyfin API authorization gist (nielsvanvelzen).

### 7. `IMediaSourceProvider` / PlaybackInfo pipeline is user-aware but wrong tool.  🟡 inferred

`MediaSourceManager` calls `IMediaSourceProvider.GetMediaSources(item, ct)`, and the `getPostedPlaybackInfo` endpoint is `userId`-aware — so this pipeline *does* see the user. But it produces media **sources** (versions/streams/containers), not segments. The only way to use it for per-user filtering would be to serve a *different, pre-edited physical file* per profile — which is **media hosting / creating a derivative**, a hard-constraint violation. Not viable for cleanyfin.

Sources: `Emby.Server.Implementations/Library/MediaSourceManager.cs`, `Jellyfin.Api/Helpers/MediaInfoHelper.cs`.

---

## Trustworthiness rating of each candidate seam

| Seam | Carries user ctx? | Can filter segments per profile? | Public/documented? | Stable across 10.10→10.11? | Verdict |
|---|---|---|---|---|---|
| `IMediaSegmentProvider.GetMediaSegments` | ❌ (ItemId only) | ❌ impossible by contract | ✅ public plugin API | ✅ stable | Trust HIGH, but user-blind — cannot do per-profile |
| `GET /MediaSegments/{itemId}` (read) | item-lookup only | ❌ returns global set | ✅ OpenAPI-documented | ✅ stable contract | HIGH trust; the surface our proxy can filter |
| Client action prefs (Skip/Ask/None) | per-client, uneven | partial, not enforced | ✅ docs | ⚠️ per-client behavior varies | LOW as an ACL — bypassable, inconsistent |
| `ISessionManager` playback events | ✅ Session/Users | ❌ can only stop/message | ✅ public interface | ❌ **broke in 10.11** | MEDIUM-LOW — fragile + wrong granularity |
| Parental/tag/schedule gates | ✅ | ❌ whole-item only | ✅ documented feature | ✅ stable | HIGH trust, wrong granularity |
| Custom plugin `ControllerBase` | ✅ (our own) | ✅ (our own logic) | ✅ supported | ✅ stable | HIGH — but native clients won't call it |
| `IMediaSourceProvider` (per-file) | ✅ | via separate files only | ✅ | ✅ | Violates metadata-only — not viable |

---

## "Do it ourselves" — the alternatives, weighed

**A. User-aware cleanyfin endpoint consumed by a cooperating client/companion.** Honest and simple, but only the companion PWA / a modified client benefits; unmodified native Jellyfin apps ignore it. Good for the marking/companion flow, insufficient as household enforcement.

**B. Per-user Jellyfin library scoping (separate item copies per profile).** Because segments key on `ItemId`, the only way to get per-profile segments natively is to give each profile *distinct item objects* (separate library folders / `.strm` per profile) each with its own filtered segment set. This technically works but **explodes setup and duplicates the catalog per kid** — a head-on collision with the "super-easy, one `docker compose up`" constraint. Reject for v1.

**C. cleanyfin reverse-proxy / companion that filters the `/MediaSegments` response per authenticated user.** A thin cleanyfin proxy sits in front of Jellyfin, reads the Jellyfin auth token on each `GET /MediaSegments/{itemId}`, maps user → cleanyfin profile, and **rewrites the response JSON** to include only the segments that profile filters. This is the **one** approach that delivers TRUE, un-bypassable-by-client-settings per-profile enforcement **to unmodified native clients**, while staying strictly metadata-only (it only drops/keeps timestamps — never touches a frame of A/V). It depends solely on the **stable public HTTP response contract**, not on any internal plugin hook, so it is insulated from the 10.11-style churn that broke the session seam. Cost: one extra hop / container (mitigated — it can be a second service in the same compose, so setup stays "add-a-container," not "run k8s"). This is "do it ourselves," done safely and within constraints.

---

## Recommendation — enforcement architecture for v1

1. **Default install (super-easy, honest, zero extra moving parts):** cleanyfin ships as a standard `IMediaSegmentProvider` that emits a **global** segment set per item (union, or a household-default curator profile's selection). Per-profile category *selection* is treated as a **client-side / opt-in** concern, and the trust boundary is documented plainly: a determined user can change client action settings or query raw `/MediaSegments` directly. This rides upstream, forks nothing, and matches "build on upstream / simplify first." It does **not** claim server-enforced per-profile filtering, because — verified from source — that does not exist in the native pipeline.

2. **Optional "enforced households" mode (real per-kid enforcement):** ship an **opt-in** cleanyfin reverse-proxy/companion (Option C) that authenticates against Jellyfin, maps user → cleanyfin profile, and filters the `/MediaSegments` response server-side in **cleanyfin's own trust domain**. Enforcement then holds even on unmodified native clients and cannot be undone by flipping a client's skip setting. Still metadata-only; still one-more-container simple.

3. **Do NOT** build enforcement on `ISessionManager` playback events (fragile, broke in 10.11, wrong granularity) or on per-user physical-file scoping (breaks super-easy setup). Use parental/tag gates only as a complementary **whole-title** layer (block a title entirely vs. filter-within), never as the segment mechanism.

**Upstream pieces we would be trusting, and how safe:**
- `IMediaSegmentProvider` plugin interface (generation) — **HIGH / stable**, but user-blind by contract (accept the limitation, don't fight it).
- `GET /MediaSegments/{itemId}` HTTP response contract (what the proxy filters) — **HIGH / OpenAPI-documented public API**; the safe thing to couple to.
- Parental/tag/schedule user policy — **HIGH / documented**; used only for whole-title gating.
- We explicitly **avoid** the low-trust internal seam (`ISessionManager` event args / `IUserManager` / `User` entity) that Jellyfin churned in 10.11.

Bottom line: per-profile enforcement is **not** obtainable from Jellyfin's server-side segment provider system — not because it's undocumented, but because the provider is handed no user context and the read path does no per-user filtering (both verified from 10.11 source). The trustworthy path to real per-profile enforcement is **cleanyfin's own thin, opt-in response-filtering layer built on the stable public `/MediaSegments` HTTP contract** — "do it ourselves," but riding the one upstream surface that is both user-attributable (via the client's own auth token) and version-stable.

---

## Open / needs runtime verification

- **Exact `/MediaSegments/{itemId}` response JSON shape on 10.11.x** (field names/casing, whether `StreamIndex`/`Action` are present) — needed to build the proxy filter reliably. 🟡 inspect a live 10.11 server + api.jellyfin.org OpenAPI.
- **Does the core segment endpoint honor the auth token's UserId in a way a proxy can read** (i.e. can the proxy reliably map token → user without a second lookup)? Likely yes (token → `/Users/Me`), but confirm the proxy can resolve user cheaply per request. 🟡 runtime test.
- **Whether jellyfin-web actually re-fetches `/MediaSegments` per playback (proxy interception point) vs. caches it** — determines whether response-rewriting is sufficient or whether some clients cache stale segments across profile switches. 🟡 runtime test with two profiles on one browser/session.
- **Confirm no plugin mechanism can post-process the core segment read** (e.g. a response filter / middleware a plugin can register) that would let us stay in-process instead of a separate proxy — quick source check of Jellyfin's middleware/DI for any `IAsyncActionFilter`/response-hook plugins can add. 🟡 source dig; if it exists it could collapse Option C into the plugin (nicer setup).
- **Android TV / webOS segment-action storage** — confirm whether any native client stores segment actions server-side per user (vs. local), which would slightly change how much the default mode "accidentally" respects profiles. 🟡 client-source/runtime.
- **10.11 `PlaybackProgressEventArgs` current shape** — the `master` source still lists a `Users` property, yet issue #14893 shows `get_Users()` was removed in a 10.11 build; reconcile whether it was removed-then-restored or renamed. Not load-bearing for the recommendation (we avoid this seam), but worth a note. 🟡 verify against a pinned 10.11.x tag.
