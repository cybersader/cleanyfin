# Spike C — Current Client Support & Mute-Action Status

> Feasibility spike from the 2026-07-21 research fan-out (workflow `cleanyfin-research`, opus, web-sourced). Purpose: build the up-to-date Media Segments client-support matrix that decides cleanyfin's MVP fleet, and pin the honest status of a native MUTE action across server + clients. Confidence + sources preserved. Distinguishes **verified from docs/source** vs **inferred — needs runtime test**.

## TL;DR

Two things changed since the "skip-only on Web + Android TV" baseline in `jellyfin-integration-mechanics.md`, and one thing did not.

1. **The skip fleet is meaningfully wider than the old MVP baseline.** As of mid-2026, native Media Segments **skip / ask-to-skip / do-nothing** support has shipped on **Jellyfin Web, Android TV (0.18+), Roku (3.0.0, Mar 2025), and Kodi (native, via `jellyfin-kodi`, landed ~early-mid 2026)**, with **webOS (LG) partial** (auto-skip works; the "ask to skip" button fails to render — issue #272). So "skip-only on Web + Android TV" is now **too conservative** — the honest demoable fleet is **Web + Android TV + Roku + Kodi**, plus webOS as degraded-but-usable. **verified**
2. **Swiftfin (iOS/tvOS) is still the notable hole.** Segment-skip is still an **open, unassigned feature request** (#1525, opened Apr 29 2025, no PR/branch as of this spike). Apple clients render **no** segment action today. **verified**
3. **A native MUTE action still does not exist anywhere — server or client.** `jellyfin-meta #30` lists Mute only as a deferred "possible future extension"; an Oct 2025 comment states "Jellyfin still does not yet have a muting API"; the 10.11.11 changelog (Jun 6 2026) contains nothing segment-related; and there is a **dedicated open feature request — #3396, "API Support for Muting Media for Content Filtering Plugin Support" (Jul 22 2025) — which is essentially cleanyfin's exact use case, still unbuilt.** The Nov-2024 Android TV post's "mute action… already in the works for future releases" has **not** materialized into any shipped client or server API 18+ months later. **verified**

Net for cleanyfin: **skip is a wider, healthier MVP surface than previously assumed; mute remains the same hard blocker with no shipped timeline.** The real-mute path is still EDL for Kodi/mpv — but the EDL plugin (`endrl/jellyfin-plugin-edl`) is **stale** (last release v0.3.0, Aug 2024, targets "10.10 unstable"; flagged incompatible with current versions), so Kodi's *native* segment support (`jellyfin-kodi` + the `jellyskip` service) is now the better-maintained Kodi path — though native Kodi segments still only **skip**, not mute. Real per-word mute stays an EDL-only, writeable-library-only capability.

## Client Support Matrix (Media Segments, as of late 2025 / mid-2026)

| Client | Skip | Ask-to-skip | Honors Action config (per type) | Native Mute | Since / evidence | Confidence |
|---|---|---|---|---|---|---|
| **Jellyfin Web** | Yes | Yes | Yes (playback settings) | **No** | Full support since 10.10; docs confirm clients decide per-type action in playback settings | 🟢 verified |
| **Android TV** (`jellyfin-androidtv`) | Yes | Yes (default for intro/outro, 8s popup) | Yes (all 5 types) | **No** | 0.18.0 (Nov 2024) added 10.10 segment support | 🟢 verified |
| **Roku** (`jellyfin-roku`) | Yes | Yes | Yes ("Auto-skip? Skip button? Nothing?" in Roku settings; segments highlighted on progress bar) | **No** | 3.0.0, released **Mar 28 2025** (issue #70) | 🟢 verified |
| **Kodi** (`jellyfin-kodi`) | Yes (native segments) | Yes | Yes | **No** (native) / **Yes via EDL** (cut+mute, stale plugin) | "Media Segments are now supported" — State of the Fin 2026-05-24; also `jellyskip` service | 🟢 verified (native skip); 🟡 EDL mute path stale |
| **webOS / LG** (`jellyfin-webos`) | Partial (auto-skip works) | **Broken** — button does not render | Partial | **No** | Issue #272: "Ask to skip does not show"; auto-skip works | 🟢 verified (partial/buggy) |
| **iOS / tvOS Swiftfin** | **No** | **No** | **No** | **No** | Feature request #1525 open + unassigned since Apr 29 2025, no PR | 🟢 verified (absent) |
| **Android mobile** (`jellyfin-android`) | Likely (web-view/ExoPlayer) | Likely | Likely | **No** | Not separately confirmed in this spike; official mobile app tracks web components | 🟡 inferred — needs runtime test |
| **Tizen / Samsung** (`jellyfin-tizen`) | Likely (packages web client) | Likely | Likely | **No** | Tizen client is essentially the packaged web UI (which supports skip); Tizen 6+ store build shipped/failed-review churn through Jan 2026 | 🟡 inferred — needs runtime test |

**Reading the matrix:** the segment *action* is chosen in each client's own playback settings from the segment's **Type** — there is no per-segment "Action" value pushed from the server that a client obeys blindly. Every shipping action is a **seek** (skip) or a **prompt** (ask-to-skip). No client exposes a mute/blur/crop primitive.

## Findings (with sources)

### F1. Skip support has expanded well beyond Web+AndroidTV — Roku and Kodi now ship it, webOS is partial. · 🟢 verified
- **Roku 3.0.0 (Mar 28 2025)** added media-segment support with full per-type action config ("Auto-skip? Display a skip button? Nothing?") and even highlights segments on a custom progress bar. (`jellyfin-roku` issue #70 → shipped.)
- **Kodi**: the official `jellyfin-kodi` add-on now natively supports Media Segments — "Media Segments are now supported" appears under Kodi in **State of the Fin 2026-05-24**. The community `jellyskip` Kodi service also talks to the Media Segments API for a skip button.
- **webOS (LG)** remains partial: **issue #272** reports the "ask to skip" button does **not** display, though auto-skip works — so segment support exists but is buggy on that client.
- Sources: <https://jellyfin.org/posts/roku-300/> · <https://github.com/jellyfin/jellyfin-roku/issues/70> · <https://jellyfin.org/posts/state-of-the-fin-2026-05-24/> · <https://github.com/jellyfin/jellyfin-webos/issues/272> · <https://github.com/SgtJalau/service.jellyskip>

### F2. Swiftfin (iOS/tvOS) still renders no segment action. · 🟢 verified
- Feature request **#1525** ("Skip button for media segments (intro/outro)") was opened **Apr 29 2025** and, as of this spike, is **open, unassigned, no branch/PR**. Apple clients are the standout gap in the fleet.
- Source: <https://github.com/jellyfin/Swiftfin/issues/1525>

### F3. Android TV: full, shipped, unchanged. · 🟢 verified
- 0.18.0 (Nov 2024) added 10.10 segment support: all five types, Skip / Ask-to-skip / Do-nothing, intros/outros default to "Ask to skip" with an 8-second auto-hiding popup.
- Source: <https://jellyfin.org/posts/androidtv-v0.18.0/>

### F4. Native MUTE action: still just proposed — NOT shipped on server or any client. · 🟢 verified
- **`jellyfin-meta #30`** (Core Segment Skipping Proposal) lists Mute in the original `SkipType`/action set but it was **deferred to "possible future extensions."** Shipped actions are skip-style only.
- **Oct 2025 comment (in #30):** *"Jellyfin still does not yet have a muting API. So Jellyfin plugins can only provide half the functionality of EDLs."*
- The Nov-2024 Android TV 0.18 post says a mute action "that temporarily silences audio" is "already in the works for future releases" — but **no shipped evidence** exists 18 months later.
- **10.11.11 changelog (Jun 6 2026)** = a single unrelated entry ("Add lockhelper for UserManager"); nothing segment- or mute-related. 10.11.0 headline features were backups, FFmpeg 7.1, EF Core, search — no mute.
- The public **media segments docs page** describes types (Intro/Outro/Recap/Preview/Commercial) and client-chosen actions but mentions **no mute action**.
- Sources: <https://github.com/jellyfin/jellyfin-meta/discussions/30> · <https://jellyfin.org/posts/androidtv-v0.18.0/> · <https://github.com/jellyfin/jellyfin/releases/tag/v10.11.11> · <https://jellyfin.org/posts/jellyfin-release-10.11.0/> · <https://jellyfin.org/docs/general/server/metadata/media-segments/>

### F5. There is a dedicated, open feature request for exactly cleanyfin's need — and it is unbuilt. · 🟢 verified
- **features.jellyfin.org #3396 — "API Support for Muting Media for Content Filtering Plugin Support" (posted Jul 22 2025).** This is precisely the VidAngel-style content-filter mute API cleanyfin would consume. It exists as a community request with **no official commitment or implementation** observed. cleanyfin should track (and could rally votes behind) this exact ticket as the upstream trigger for native mute.
- Source: <https://features.jellyfin.org/posts/3396/api-support-for-muting-media-for-content-filtering-plugin-support>

### F6. EDL is still the only real per-segment MUTE path — but the Jellyfin EDL plugin is stale. · 🟡 verified w/ caveat
- `endrl/jellyfin-plugin-edl` converts segments → Kodi/mpv `.edl` with a configurable per-type action, and the EDL format natively supports **1 = mute** and **0 = cut** — a genuine mute where native clients cannot.
- **Caveat (new):** last release is **v0.3.0, Aug 31 2024**, and the requirements still read **"⚠️ Jellyfin 10.10 unstable"**; a 2026 source flags it as **incompatible with current Jellyfin versions**. It also hard-requires a **writeable media library** ("You can't use this plugin with read-only media libraries!") since it writes `.edl` next to each file. Treat it as a **reference format, not a dependable dependency** — cleanyfin may need to generate EDL itself.
- For Kodi *skip*, the maintained path is now native `jellyfin-kodi` segments + `jellyskip`; for Kodi *mute/cut*, EDL is still the only route and carries the staleness + writeable-library caveats.
- Sources: <https://github.com/endrl/jellyfin-plugin-edl> · <https://kodi.wiki/view/Edit_decision_list>

## Recommendation

**Update the MVP fleet from "skip-only on Web + Android TV" to "skip-only on Web + Android TV + Roku + Kodi (native), with webOS partial and Swiftfin/iOS as the known gap."** This is the honest, current baseline and it is materially better for a demo than the old two-client story. Keep every objectionable span represented as a **skip** segment.

**Hold mute as an upstream-blocked, unscheduled capability.** No server API, no client action, 18 months after it was called "in the works." Do NOT design the MVP around native mute. Two concrete moves:
1. Track (and consider mobilizing votes on) **feature request #3396** and **jellyfin-meta #30** as the explicit triggers to add a native mute code path.
2. Offer EDL **mute/cut** only as an opt-in Kodi/mpv workflow, and be prepared to **emit EDL from cleanyfin's own data** rather than lean on the stale `jellyfin-plugin-edl`. Gate it behind a writeable-library check.

**Implication for the category taxonomy:** because every profanity/violence/nudity span collapses to a coarse skip on all native clients today, single-word profanity filtering will feel worse than VidAngel (drops several seconds of video, not just the word). This is a product-honesty point for the roadmap, not a fixable engineering detail, until upstream mute lands.

## Open / needs runtime verification

- **Android mobile (`jellyfin-android`)**: confirm whether the official mobile app renders skip/ask-to-skip on 10.11 (inferred yes via shared web/ExoPlayer components; not directly verified this spike). *Runtime test: configure a segment, play on the Android app, observe.*
- **Tizen (`jellyfin-tizen`)**: confirm segment skip in the Samsung-store build (inferred yes since it packages the web client, but the client went through review-failure churn into Jan 2026). *Runtime test on a Tizen 6+ device.*
- **webOS**: verify current state of issue #272 — is auto-skip still the only working action, or has the ask-to-skip button been fixed in a newer webOS release? *Runtime test on LG.*
- **Roku/Kodi action granularity**: confirm they expose the same three actions per type as Web/AndroidTV and that "do nothing" reliably suppresses skips (matters for per-profile opt-out UX). *Runtime test.*
- **EDL plugin viability on 10.11**: does `endrl/jellyfin-plugin-edl` actually load/run on 10.11.11, or is it broken as the 2026 note suggests? If broken, cleanyfin must own EDL generation. *Install test against a 10.11.11 server.*
- **Native mute timeline**: watch #3396 / #30 / 10.12 milestone for any actual server `MediaSegmentAction`/mute API — the single feature that unlocks the true VidAngel profanity experience.

## Sources
- [Media segments — Jellyfin docs](https://jellyfin.org/docs/general/server/metadata/media-segments/)
- [jellyfin-meta Discussion #30 — Core Segment Skipping Proposal](https://github.com/jellyfin/jellyfin-meta/discussions/30)
- [Feature request #3396 — API Support for Muting Media for Content Filtering](https://features.jellyfin.org/posts/3396/api-support-for-muting-media-for-content-filtering-plugin-support)
- [Swiftfin #1525 — Skip button for media segments (open)](https://github.com/jellyfin/Swiftfin/issues/1525)
- [Jellyfin for Android TV 0.18](https://jellyfin.org/posts/androidtv-v0.18.0/)
- [Roku 3.0.0 release (media segments)](https://jellyfin.org/posts/roku-300/) · [jellyfin-roku #70](https://github.com/jellyfin/jellyfin-roku/issues/70)
- [jellyfin-webos #272 — Ask to skip does not show](https://github.com/jellyfin/jellyfin-webos/issues/272)
- [State of the Fin 2026-05-24 (Kodi media segments)](https://jellyfin.org/posts/state-of-the-fin-2026-05-24/)
- [jellyfin-plugin-edl (stale; writeable-library requirement)](https://github.com/endrl/jellyfin-plugin-edl) · [Kodi EDL format](https://kodi.wiki/view/Edit_decision_list)
- [Jellyfin 10.11.11 release changelog](https://github.com/jellyfin/jellyfin/releases/tag/v10.11.11) · [Jellyfin 10.11.0 release](https://jellyfin.org/posts/jellyfin-release-10.11.0/)
- [jellyskip Kodi service](https://github.com/SgtJalau/service.jellyskip)
