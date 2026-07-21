---
title: Jellyfin integration mechanics
description: Technical feasibility of building cleanyfin on Jellyfin's Media Segments API — skip works today, mute is the blocker, per-profile enforcement is a gap.
sidebar:
  order: 3
---

*A research deep-dive from the 2026-07-21 cleanyfin research fan-out. Lightly formatted raw findings with confidence levels and sources preserved.*

## TL;DR

Jellyfin's Media Segments feature (server core since 10.10.0, Nov 2024; matured in 10.11) is a near-perfect foundation for cleanyfin: it stores typed, timestamped segments (ItemId + StreamIndex + Type + StartTicks/EndTicks + Action) that any client can honor, and cleanyfin can ship as a standard IMediaSegmentProvider plugin that pulls from a crowdsourced DB — exactly the SponsorBlock/Intro Skipper pattern, which is the proven reference implementation. The SKIP path is fully buildable today: Web, Android TV (0.18+), and others already render skip / "ask to skip" / auto-skip for all segment types. The MUTE path is the hard blocker — as of 10.11 (mid-2026) Jellyfin still has NO mute action in clients; only skip-style actions exist, so VidAngel-style "mute the profanity, keep the video" is NOT possible natively yet and is only a roadmap item. VISUAL masking / crop for nudity does not exist at all. A realistic MVP: crowdsourced provider + skip-only enforcement on Web/Android TV now, EDL export (endrl/jellyfin-plugin-edl) for mute/cut on Kodi/mpv, and a companion PWA for in-playback marking via the Jellyfin session/position API plus the create/delete MediaSegments HTTP endpoints (now folded into Intro Skipper on 10.11). Per-profile enforcement is a real gap: segments are global per-item, not per-user, so cleanyfin must implement its own profile-to-category filter layer.

## Key Findings

### 1. Media Segments are a first-class Jellyfin server feature since 10.10.0 (Nov 2024); the data model is ItemId + StreamIndex + Type + StartTicks/EndTicks + Action.  ·  🟢 high

Introduced in the 10.10.0 release. A media segment is defined as 'a moment in time of a media stream (ItemId+StreamIndex) with Type and possible Action applicable between StartTicks/EndTicks.' Ticks are .NET DateTime ticks (100-nanosecond units; 10,000,000 ticks = 1 second). For 10.10 the server only provided the storage structure — a plugin is required to create segments. Segments differ from chapters specifically by carrying a Type so clients can act differently per type.

Sources: <https://jellyfin.org/docs/general/server/metadata/media-segments/> · <https://jellyfin.org/posts/jellyfin-release-10.10.0/> · <https://pub.dev/documentation/jellyfin_dart/latest/jellyfin_dart/MediaSegmentDto-class.html>

### 2. Segment types are Intro, Outro, Preview, Recap, Commercial, plus Unknown/Annotation — a fixed enum cleanyfin cannot extend server-side.  ·  🟢 high

The MediaSegmentType enum values are Intro, Outro, Recap, Preview, Commercial, and an Unknown/Annotation catch-all (the Kotlin SDK lists 'Annotation'; the core proposal listed 'Unknown'). There is NO 'Profanity', 'Violence', or 'Nudity' type. cleanyfin's content categories do not map onto Jellyfin's type enum — cleanyfin would have to overload existing types (e.g. reuse 'Commercial'/'Unknown') OR carry its own category metadata externally and translate at provider time. This is a structural constraint: the crowdsourced category taxonomy lives in cleanyfin's DB, not in Jellyfin's segment type field.

Sources: <https://kotlin-sdk.jellyfin.org/dokka/jellyfin-model/org.jellyfin.sdk.model.api/-media-segment-type/index.html> · <https://github.com/jellyfin/jellyfin-meta/discussions/30>

### 3. cleanyfin CAN be a MediaSegmentProvider that pulls from a crowdsourced DB — this is the exact Intro Skipper pattern and is proven.  ·  🟢 high

Plugins implement the IMediaSegmentProvider interface; the core method is GetMediaSegments, which returns typed segments for a given item. The official jellyfin-plugin-chapter-segments is a full working C# example (stateless service, produces segments from chapter data via regex). Intro Skipper is the flagship provider: it detects intros/credits via chromaprint audio fingerprinting and black-frame detection, writes segments into Jellyfin, and clients render the skip UI natively. Since Jellyfin 10.10, 'Intro Skipper does NOT modify the UI' — it relies entirely on the client-native segment skip button. This proves cleanyfin's architecture: a provider that, instead of analyzing audio locally, fetches community-tagged timestamps from a federated DB and emits them as segments.

Sources: <https://github.com/intro-skipper/intro-skipper> · <https://deepwiki.com/jellyfin/jellyfin-plugin-chapter-segments/2.2-chapter-media-segment-provider> · <https://deepwiki.com/intro-skipper/intro-skipper/4.3-auto-skip-and-playback-integration>

### 4. SKIP-style actions work in real clients today: Web (full), Android TV 0.18+ (full), with Skip / Ask-to-skip / Do-nothing per type.  ·  🟢 high

The three shipping client actions are: Skip (immediately seeks to segment end), Ask to skip (shows a dismissable popup / skip button), and Do nothing (ignore). Web interface 'fully supports skipping segments.' Android TV 0.18 added initial 10.10 support with all five segment types and defaults intros/outros to 'Ask to skip' (popup auto-hides after 8s). Actions are configured in each client's playback settings, per client. Client behavior is decided client-side from the segment metadata the server serves via GET /MediaSegments/{itemId}.

Sources: <https://jellyfin.org/posts/androidtv-v0.18.0/> · <https://jellyfin.org/docs/general/server/metadata/media-segments/> · <https://github.com/jellyfin/jellyfin-meta/discussions/30>

### 5. MUTE is the make-or-break blocker: as of Jellyfin 10.11 (mid-2026) there is still NO mute action in the client segment system — only skip-style actions exist.  ·  🟢 high

The Core Segment Skipping Proposal (jellyfin-meta #30) lists Mute as a proposed action, but it was deferred to 'possible future extensions.' Commenters as of Oct 2025 note 'Jellyfin still does not yet have a muting API.' Release coverage of 10.11 describes a mute action ('temporarily silences audio') as 'already in the works for future releases' — i.e. NOT shipped. This directly breaks the VidAngel core behavior of muting profanity while video continues. cleanyfin cannot natively mute a word of dialogue on any mainstream Jellyfin client today; it can only skip past the segment (which drops both audio AND video for that span).

Sources: <https://github.com/jellyfin/jellyfin-meta/discussions/30> · <https://jellyfin.org/docs/general/server/metadata/media-segments/>

### 6. VISUAL masking / cropping / black-out for nudity does not exist in Jellyfin at any layer — no client-side video overlay or crop capability.  ·  🟢 high

Jellyfin clients only expose seek (skip) and, on the roadmap, mute. There is no per-segment video blur, black-box, crop, or overlay primitive anywhere in the segment system or client players. VidAngel's crop-for-nudity behavior has no Jellyfin analog and would require either full skip of the scene, or server-side transcoding that burns a mask into the video — which is heavy, not real-time per-user, and arguably closer to creating a derivative work. For an MVP, nudity is handled only by skip.

Sources: <https://github.com/jellyfin/jellyfin-meta/discussions/30> · <https://jellyfin.org/docs/general/server/metadata/media-segments/>

### 7. EDL export is the realistic fallback for mute/cut and cross-player portability (Kodi, mpv, MPlayer).  ·  🟢 high

endrl/jellyfin-plugin-edl converts Jellyfin media segments (Intro, Outro, etc.) into standard .edl files with a configurable 'Edl Action' per segment type. It requires a WRITEABLE media library (writes the .edl next to the media file) and Jellyfin 10.10+. The Kodi/MPlayer EDL format natively supports action codes including 0=cut, 1=mute, 2=scene-marker, 3=commercial-break — meaning MUTE is achievable on Kodi/mpv playback where it is NOT on native Jellyfin clients. This gives cleanyfin a portable interchange format and a real mute path for users who play via Kodi. Downside: requires write access to the library and only benefits EDL-aware players.

Sources: <https://github.com/endrl/jellyfin-plugin-edl> · <https://kodi.wiki/view/Edit_decision_list> · <https://github.com/jellyfin/jellyfin/issues/109>

### 8. A create/delete HTTP API for segments exists — critical for cleanyfin's crowdsourced write path and companion marking app.  ·  🟡 medium

Native Jellyfin only exposes GET /MediaSegments/{itemId} to read segments; there was no built-in HTTP endpoint to WRITE segments (writes normally happen server-side via the provider interface at scan time). endrl / intro-skipper's jellyfin-plugin-ms-api ('Extends the Jellyfin MediaSegments HTTP API with create and delete endpoints', Jellyfin 10.10) filled this gap. That plugin is now OBSOLETE because 'the functionality is included in the 10.11 Jellyfin release of the Intro Skipper plugin.' Implication: on 10.11+, a companion app can programmatically create/delete segments over HTTP, enabling live crowdsourced marking. cleanyfin should either depend on that API or ship its own controller (the ms-api source is the reference).

Sources: <https://github.com/intro-skipper/jellyfin-plugin-ms-api> · <https://github.com/endrl/jellyfin-plugin-ms-api> · <https://typescript-sdk.jellyfin.org/functions/generated-client.MediaSegmentsApiFactory.html>

### 9. In-playback MARKING is feasible via a companion PWA using the Jellyfin session/playback-position API.  ·  🟢 high

Clients report playback state through POST /Sessions/Playing (start), POST /Sessions/Playing/Progress (progress, carries PositionTicks in PlaybackProgressInfo), and POST /Sessions/Playing/Stopped. A companion web/mobile app authenticated to Jellyfin can read the active session's current PositionTicks in near-real-time, let the viewer stamp in/out points, tag a category, and POST the resulting segment to the create endpoint. This is the realistic MVP for 'mark while watching' WITHOUT modifying any Jellyfin client: a separate PWA that talks to the same Jellyfin server, mirroring how SponsorBlock's submission UI is separate from the player. Note the companion cannot inject custom buttons into the native Jellyfin players themselves (no official client plugin/UI-extension API); it runs alongside.

Sources: <https://typescript-sdk.jellyfin.org/interfaces/generated-client.PlaybackProgressInfo.html> · <https://deepwiki.com/jellyfin/jellyfin-apiclient-python/3.5-remote-control-and-playback>

### 10. Per-USER / per-profile enforcement is a genuine gap: segments are global per media item, not per-account, so cleanyfin must build its own profile filter layer.  ·  🟢 high

A MediaSegmentProvider runs server-side at scan time and produces one set of segments per item; GET /MediaSegments/{itemId} returns the SAME segments to every user. Segment ACTIONS (skip/ask/none) are chosen in each CLIENT's playback settings, not enforced server-side as a per-profile ACL. Jellyfin's real per-user controls are parental (MaxParentalRating), allowed/blocked tags, and access schedules — these gate whole items, not sub-item segments. Therefore cleanyfin's 'per-profile category settings' and 'per-title request bypass' cannot be enforced purely by the native segment system; cleanyfin needs its own mapping (which categories a profile filters) applied either by (a) filtering which segments its provider emits — but that is global, not per-user — or (b) a cleanyfin-controlled playback layer / client config. This is the weakest link and needs an explicit design decision.

Sources: <https://jellyfin.org/docs/general/server/metadata/media-segments/> · <https://github.com/jellyfin/jellyfin-meta/discussions/30>

### 11. Client support is UNEVEN and version-gated — plan for a heterogeneous fleet.  ·  🟢 high

Web: full skip support. Android TV: 0.18+ (requires app update, 10.10 server). Swiftfin (iOS/tvOS): skip button for media segments was still an OPEN feature request (#1525, opened Apr 29 2025) — Apple clients lagged. webOS (LG TV): 'Ask to skip does not show' bug (#272) indicates incomplete support. Kodi/mpv: only via EDL export, not native segments. Intro Skipper on 10.11 requires Jellyfin 10.11.11+ and the Jellyfin ffmpeg fork 7.1.1-7+. Net: cleanyfin can rely on Web + Android TV for a demoable MVP, must treat iOS/tvOS and smart-TV clients as best-effort, and should lean on EDL for the long tail.

Sources: <https://github.com/jellyfin/Swiftfin/issues/1525> · <https://github.com/jellyfin/jellyfin-webos/issues/272> · <https://github.com/intro-skipper/intro-skipper>

### 12. Jellyfin plugins are C#/.NET, packaged as DLLs, distributed via a manifest.json repository URL, with scheduled tasks and a config UI — a low-friction, proven install path.  ·  🟢 high

Plugins are .NET assemblies built from the jellyfin/jellyfin-plugin-template. They register via dependency injection (implementing interfaces like IMediaSegmentProvider), expose scheduled tasks (e.g. Intro Skipper's 'Detect and Analyze Media Segments' task), and ship an embedded HTML config page. Distribution is through a plugin catalog: the admin adds a repository URL (e.g. Intro Skipper's https://manifest.intro-skipper.org/manifest.json) in Dashboard > Plugins > Repositories, then installs and restarts. This means cleanyfin can be installed by a non-developer in a few clicks once a manifest is hosted — aligns with the 'super-easy setup' constraint. Optional web-UI tweaks (e.g. skip-button timeout) require the separate File Transformation plugin.

Sources: <https://github.com/jellyfin/jellyfin-plugin-template> · <https://github.com/intro-skipper/intro-skipper> · <https://jellywatch.app/blog/jellyfin-intro-skipper-chapters-plugins-quality-of-life-2026>

## Recommendations for cleanyfin

**R1. Build cleanyfin's core as a standard IMediaSegmentProvider plugin (clone the Intro Skipper / chapter-segments architecture) whose GetMediaSegments fetches community-tagged timestamps from the federated DB instead of analyzing audio. Ship a manifest.json plugin repo for one-click install.**

- *Why:* This is the proven, upstream-blessed pattern; it inherits native skip UI on Web/Android TV for free, requires zero client forks, and matches the 'simplify, don't over-engineer' and 'super-easy setup' constraints. It is DMCA-safe because it distributes only timestamps/types (edit-decisions), exactly like SponsorBlock.
- *Risk / tradeoff:* You are locked into Jellyfin's fixed segment-type enum (no Profanity/Nudity types) and to skip-only actions until upstream ships mute. The provider output is global per item, not per user.

**R2. Ship SKIP-only as the MVP filtering behavior; represent every objectionable span as a skip segment. Explicitly document that mute (audio-only) is not yet possible on native clients and is gated on upstream.**

- *Why:* Skip is the only action working across Web + Android TV today, so an MVP that only skips is genuinely usable now and demoable. Being honest about mute prevents overpromising the VidAngel experience.
- *Risk / tradeoff:* Skip drops both audio and video for the span, which is a coarser, more jarring edit than VidAngel's word-level mute; profanity that is a single word forces skipping several seconds of video. This will feel worse than VidAngel for dialogue-heavy filtering.

**R3. Add EDL export (fork/depend on endrl/jellyfin-plugin-edl) so Kodi/mpv users get true per-segment MUTE (EDL action 1) and cut (action 0), and so cleanyfin has a portable, player-agnostic interchange format for its edit-decision data.**

- *Why:* EDL is the ONLY path to real muting today and works across Kodi/mpv/MPlayer, hedging against Jellyfin client limitations. It also doubles as a clean export format for the federated DB, reinforcing the 'we only ship timestamps' DMCA posture.
- *Risk / tradeoff:* EDL export requires a writeable media library (writes .edl next to media), which many self-hosters run read-only; and it only benefits EDL-aware external players, not the native Jellyfin apps.

**R4. Build in-playback marking as a SEPARATE companion PWA that authenticates to Jellyfin, reads the live session PositionTicks (POST /Sessions/Playing/Progress data / session polling), lets the user stamp in/out + category, and POSTs to the MediaSegments create endpoint (folded into Intro Skipper on 10.11; use ms-api source as reference).**

- *Why:* There is no official client-side UI-extension API to add a 'mark' button inside the native players, so a side-car PWA is the only realistic MVP and mirrors SponsorBlock's separate submission flow. It works across all clients because it only needs the server API.
- *Risk / tradeoff:* UX is two-app (watch in Jellyfin, mark in the PWA) rather than one integrated button; live position accuracy depends on client progress-report cadence; the write endpoint's stability across 10.11+ needs verification since ms-api was absorbed and its exact route may change.

**R5. Design cleanyfin's own per-profile category filter layer as an explicit component — do NOT assume Jellyfin enforces per-user segment policy. Decide early between (a) per-user Jellyfin instances/libraries, (b) cleanyfin-managed client action config, or (c) accepting global-per-item segments with client-side opt-in.**

- *Why:* Segments are global per item and actions are per-client-install, so 'per-profile settings decide what a viewer can see' is NOT natively enforced. Naming this gap up front avoids designing an MVP that silently leaks filtered content to the wrong profile.
- *Risk / tradeoff:* Any per-profile enforcement cleanyfin adds lives outside Jellyfin's security model, so a determined user can bypass it by changing client settings or querying segments directly; true enforcement may require per-user server scoping, which complicates the 'super-easy' single-instance setup.

**R6. Target Jellyfin 10.11+ as the baseline and treat Web + Android TV as the supported MVP fleet; mark Swiftfin/webOS/other clients best-effort and track upstream mute-action progress (jellyfin-meta #30) as the trigger to add native mute.**

- *Why:* 10.11 matured the segment system and absorbed the create/delete API; Web and Android TV have real, shipping skip support. Pinning the baseline keeps setup simple and avoids supporting half-broken older client behavior.
- *Risk / tradeoff:* Requiring 10.11 excludes users on older servers; iOS/tvOS (Swiftfin) and smart-TV users get a degraded (or no-op) filtering experience until those clients ship segment support, which is outside cleanyfin's control.

## Open Questions

- **On Jellyfin 10.11+, what is the exact, current HTTP route and payload to CREATE/DELETE a media segment now that ms-api was absorbed into Intro Skipper? (Needed to build the companion PWA write path.)** — *lean:* Inspect the Intro Skipper 10.11 source and the OpenAPI spec at api.jellyfin.org directly; assume a POST that takes ItemId, Type, StartTicks, EndTicks (and StreamIndex) and requires an admin/API token.
- **Does cleanyfin overload Jellyfin's fixed segment-type enum (e.g. map profanity->Commercial, nudity->Unknown) or carry its own category taxonomy externally and translate per-request?** — *lean:* Carry the rich category taxonomy in cleanyfin's federated DB and translate to the nearest Jellyfin type at provider emit time, so the crowdsourced data model is not crippled by Jellyfin's 6-value enum.
- **How is per-profile enforcement actually delivered given segments are global per item — per-user Jellyfin scoping, cleanyfin-managed client config, or accepting client-side opt-in only?** — *lean:* For MVP accept client-side opt-in (honest about the trust boundary); design a per-user enforcement layer as a fast-follow rather than blocking the MVP on it.
- **Will Jellyfin ship a native client MUTE action, and on what timeline, since it is the single feature that unlocks the true VidAngel profanity-filter experience?** — *lean:* Treat mute as upstream-dependent and unscheduled; do not block the MVP on it — deliver skip now + EDL-mute for Kodi/mpv, and add native mute when jellyfin-meta #30 lands.
- **Is writing .edl files next to media (writeable library requirement) acceptable given many self-hosters run read-only libraries, or should EDL be served via API/sidecar instead?** — *lean:* Prefer serving edit-decisions via the segment API for native clients and only generate EDL on demand for users who explicitly opt into Kodi/mpv workflows, avoiding a hard writeable-library dependency in the default install.

## Sources

- [Media segments — Jellyfin official docs](https://jellyfin.org/docs/general/server/metadata/media-segments/) — Primary: segment definition, types, that plugins create them, Web fully supports skipping, client-configured actions.
- [Jellyfin 10.10.0 release announcement](https://jellyfin.org/posts/jellyfin-release-10.10.0/) — Primary: Media Segments introduced 10.10; only storage structure provided; Chapter Segments Provider is the official example; Web supports skipping, other clients pending.
- [Core Segment Skipping Proposal — jellyfin-meta Discussion #30](https://github.com/jellyfin/jellyfin-meta/discussions/30) — Primary design doc: proposed actions (skip/mute/prompt), segment types, MVP targeting 10.10, and mute deferred / 'no muting API' as of Oct 2025.
- [Intro Skipper plugin (reference MediaSegmentProvider)](https://github.com/intro-skipper/intro-skipper) — Reference implementation: chromaprint detection, writes segments, no UI modification (native skip button), requires 10.11.11+ and Jellyfin ffmpeg fork.
- [Intro Skipper Auto-Skip & Playback Integration (DeepWiki)](https://deepwiki.com/intro-skipper/intro-skipper/4.3-auto-skip-and-playback-integration) — How the provider + client skip loop works: monitors position, seeks to segment end, integrates with the standard segment system.
- [Chapter Media Segment Provider (DeepWiki)](https://deepwiki.com/jellyfin/jellyfin-plugin-chapter-segments/2.2-chapter-media-segment-provider) — Concrete IMediaSegmentProvider / GetMediaSegments example — the simplest provider architecture to clone.
- [jellyfin-plugin-ms-api (create/delete segment HTTP endpoints)](https://github.com/intro-skipper/jellyfin-plugin-ms-api) — Extends MediaSegments HTTP API with create/delete; now obsolete because folded into Intro Skipper on 10.11 — reference for cleanyfin's write path.
- [jellyfin-plugin-edl (segments -> EDL export)](https://github.com/endrl/jellyfin-plugin-edl) — Converts media segments to Kodi/mpv .edl with per-type action; requires writeable library and 10.10+ — the mute/cut fallback path.
- [Kodi EDL format spec](https://kodi.wiki/view/Edit_decision_list) — EDL action codes (0=cut, 1=mute, 2=scene, 3=commercial) — proves mute is achievable on Kodi/mpv where native Jellyfin cannot.
- [Jellyfin for Android TV 0.18 release](https://jellyfin.org/posts/androidtv-v0.18.0/) — Android TV client segment support: all five types, Skip / Ask-to-skip / Do-nothing, ask-to-skip default, 8s popup.
- [Swiftfin issue #1525 — skip button for media segments](https://github.com/jellyfin/Swiftfin/issues/1525) — Evidence iOS/tvOS lagged: feature request opened Apr 29 2025, segment skip not yet implemented in Swiftfin.
- [jellyfin-webos issue #272 — Ask to skip does not show](https://github.com/jellyfin/jellyfin-webos/issues/272) — Evidence LG/webOS segment support is incomplete/buggy — plan for uneven smart-TV support.
- [PlaybackProgressInfo (Jellyfin TS SDK)](https://typescript-sdk.jellyfin.org/interfaces/generated-client.PlaybackProgressInfo.html) — PositionTicks field + POST /Sessions/Playing/Progress — the position source for a companion marking PWA.
- [Jellyfin plugin template](https://github.com/jellyfin/jellyfin-plugin-template) — Canonical C#/.NET plugin scaffold: DI registration, scheduled tasks, embedded config UI, manifest-based catalog distribution.
- [MediaSegmentType enum (Kotlin SDK)](https://kotlin-sdk.jellyfin.org/dokka/jellyfin-model/org.jellyfin.sdk.model.api/-media-segment-type/index.html) — Authoritative enum values (Intro/Outro/Recap/Preview/Commercial/Annotation) — confirms no content-category types exist.
