# Upstream Jellyfin direction, non-Android-TV clients, and prior art

Research notes, 2026-11-21. Source reading and public pages only; **no client route in this file was run**. Tags: VERIFIED-FROM-SOURCE (pinned source read), DOCUMENTED (public page or thread), INFERRED. Only the release versions in the first bullet were double-checked against the GitHub releases API.

## Summary

- **Versions.** Jellyfin server and web are at v12.4 (2026-10-26); 12.0 went stable 2026-09-07 (10.12 was renamed). Android TV app is v0.20.0 (2026-11-10). The Cleanyfin lab and plugin still target 10.11.11.
- **Upstream direction.** Media Segments are plugin-supplied timestamps with client-chosen behaviour. The segment DTO has five fields (Id, ItemId, Type, StartTicks, EndTicks) and a closed type enum in v12.4 and at head `d05a80c`; the TypeScript, Kotlin, Swift and C# SDKs agree. Mute/skip suggestions, extra types and per-user segments were deferred and are not on a published roadmap. Maintainers hold that a client acts on a segment's type, not its origin.
- **Where a no-own-client route looks real:** desktop players built on mpv (jellyfin-mpv-shim, Jellyfin Desktop, Jellium) via a user mpv config or script; Android TV via mpv-based third-party clients (see `google-tv-clients.md`).
- **Where it does not, today:** Web and its wrappers (no exact cut in the stock engine), Tizen (bundled web UI, no server injection), Roku, Xbox, iOS/tvOS, Android phone, Kodi (lab cut leaked).
- **People worth talking to:** Michael Corrigan (CorrMedia; author of the open server hooks proposal), Jared Skinner (JellyFilter), Matthew Bloomfield (second jellyfilter), Jacob Willden (MovieContentFilter), Francisco Ruiz (VideoSkip exchange).
- **Contribution constraint.** Jellyfin's LLM policy forbids LLM-written public communication (issues, comments, PR bodies must be written by the human); code assistance is allowed if understood and tested: <https://jellyfin.org/docs/general/contributing/llm-policies/>. DOCUMENTED.

## Part 1: upstream

| Item | State | What was said | Tag |
| --- | --- | --- | --- |
| [MediaSegmentDto at head](https://github.com/jellyfin/jellyfin/blob/d05a80ced13a36a5d1fc328f6c83775d4b6021b4/MediaBrowser.Model/MediaSegments/MediaSegmentDto.cs) | Five fields; controller is read-only GET with `includeSegmentTypes`; `IMediaSegmentProvider` emits the same DTO, cached per item/provider, enabled and ordered per library | n/a | VERIFIED |
| [jellyfin-meta discussion #30](https://github.com/jellyfin/jellyfin-meta/discussions/30) | Opened 2022-09-19, closed 2026-03-28 without an accepted answer | Niels van Velzen's 2024-06-01 MVP deferred suggested mute/skip, added types and default user actions ([comment](https://github.com/jellyfin/jellyfin-meta/discussions/30#discussioncomment-9629257)). 2025-01-06: "There is currently no active development for the EDL functionality", inviting a new meta design first ([comment](https://github.com/jellyfin/jellyfin-meta/discussions/30#discussioncomment-11754124)); JPVenson suggested plugin conversion; thornbill: EDL might need more segment types, "otherwise a plugin would be ideal" ([comment](https://github.com/jellyfin/jellyfin-meta/discussions/30#discussioncomment-11754309)) | DOCUMENTED |
| [Feature request #3396, mute API for content-filtering plugins](https://features.jellyfin.org/posts/3396/api-support-for-muting-media-for-content-filtering-plugin-support) | Open since 2025-07-22, 5 votes, 3 comments, no staff reply | Michael Corrigan (2026-09-29) links #17808; Matthew Bloomfield (2026-11-02): "I have a plugin locally running to do this but it's a bit hacky without an API!" | DOCUMENTED |
| [jellyfin #17807 / #17808](https://github.com/jellyfin/jellyfin/issues/17807) | Open since 2026-09-06; no maintainer comment or review yet | Proposes per-session FFmpeg audio/filter graphs during transcoding; direct play unchanged; not a native segment action | DOCUMENTED |
| [jellyfin #18129](https://github.com/jellyfin/jellyfin/pull/18129#issuecomment-5740690978) | Rejected 2026-09-19 | "A client should act based on the segment type, not the origin" | DOCUMENTED |
| [jellyfin-web #6200](https://github.com/jellyfin/jellyfin-web/pull/6200#issuecomment-2417438060) | Closed unmerged 2024-12-29 | Shifting boundaries is the provider's job, "definitely not up to the client" | DOCUMENTED |
| [jellyfin #10530](https://github.com/jellyfin/jellyfin/pull/10530#issuecomment-2251358692) | Closed unmerged 2024-07-26 | Server team wanted "a very tight scope"; per-user config called "non-trivial" | DOCUMENTED |
| [jellyfin #12833](https://github.com/jellyfin/jellyfin/pull/12833#issuecomment-2440826153) | Open since 2024-10-16 | "we do not aim for pixel perfect precision" (said about de-duplication) | DOCUMENTED |
| [jellyfin-androidtv #4567](https://github.com/jellyfin/jellyfin-androidtv/pull/4567#issuecomment-2868939579) | Closed 2025-05-10 | Subtitle-driven profanity filter closed "as invalid and stale"; not a stated blanket policy | DOCUMENTED |
| jellyfin-web #8509, #8513 | Closed 2026-09-18 and 2026-10-01 | Closed citing the LLM policy (and inconsistent official apps), not a rejection of the feature idea | DOCUMENTED |
| [Android TV 0.18 post](https://jellyfin.org/posts/androidtv-v0.18.0/) | 2024-11-30 | Mute called "already in the works for future releases"; nothing in 12.4 or tracked PRs fulfils it | DOCUMENTED |
| [GeneralCommandType](https://github.com/jellyfin/jellyfin/blob/v12.4/MediaBrowser.Model/Session/GeneralCommandType.cs#L28-L31) | Exists | Generic remote Mute/Unmute commands; not a timed range | VERIFIED |
| Parental controls | Exists | Item visibility and access windows only; no within-title policy found | DOCUMENTED |

Smallest core change that would let clients recognise a filter mark (INFERRED; nobody has proposed it): an additive, nullable, namespaced category (and optional action hint) on the DTO and persisted entity, with explicit client opt-in. A single new built-in type is the narrower alternative. Either still needs SDK and client work.

## Part 2: clients other than Android TV

| Platform / client | Engine and segment handling | Route without owning a client | Tag |
| --- | --- | --- | --- |
| Jellyfin Web v12.4 and wrappers | `timeupdate` then seek; sub-second auto-skip and under-3 s prompt excluded ([source](https://github.com/jellyfin/jellyfin-web/blob/v12.4/src/apps/legacy/features/playback/utils/mediaSegmentManager.ts)) | Server-hosted injection reaches Web; no exact cut in the stock engine; scheduled mute unproven | VERIFIED / INFERRED |
| webOS | Hosts the server's web UI; the ask-to-skip bug (#272) was confirmed fixed 2025-02-10 | Same limits as Web | DOCUMENTED |
| Tizen | Bundles its own web build | No server injection | DOCUMENTED |
| Jellyfin Desktop v2.1.0-pre2 | QtWebEngine + libmpv; per-profile user `mpv.conf` ([README](https://github.com/jellyfin/jellyfin-desktop/blob/v2.1.0-pre2/README.md#file-locations)); loads the URL via libmpv `loadfile` | Local mpv config or script; server cannot push scripts | VERIFIED |
| Jellium (independent fork of the desktop rewrite) | CEF + mpv; documents standard `mpv.conf` and scripts | Same; active, no formal releases | DOCUMENTED |
| jellyfin-mpv-shim v3.3.0 | mpv; `mpv.conf`, `input.conf`, custom Lua in `scripts/` ([docs](https://github.com/jellyfin/jellyfin-mpv-shim/blob/v3.3.0/docs/configuration.md#mpv-configuration)); five native types, ordinary seek | Best desktop route: an installed local script or pre-play kept-range adapter. EDL URL auth, timeline remapping and audio timing UNVERIFIED | DOCUMENTED |
| Swiftfin 1.7.1 | MPV default or AVPlayer; no script mechanism documented | Upstream ask only | DOCUMENTED |
| Jellyfin Mobile (iOS) v1.8.1.1 | Expo WebView | Web limits | INFERRED |
| Infuse | Proprietary; developer said it "will also be able to recognize Recap, Commercials, and Unknown segments" ([post](https://community.firecore.com/t/infuse-8-0-4-now-available/52332/16)) | Vendor request; recognising is not verified skipping | DOCUMENTED |
| Streamyfin v0.55.2 | MPV primary; five types, tick-driven seek; no user script/EDL hook seen | Upstream ask | VERIFIED |
| Official Android phone v2.7.3 | ExoPlayer skip, excludes Unknown (#1507), ask button checks every second (#1685) | Upstream ask | DOCUMENTED |
| Findroid v1.2.0 | ExoPlayer/mpv; native segments | Upstream ask | DOCUMENTED |
| Roku 3.2.4 | SceneGraph; Skip/Ask/None | None evidenced | VERIFIED |
| Xbox 0.9.8 | UWP web wrapper, native/VLC options | None evidenced | VERIFIED |
| Kodi | jellyfin-kodi drops Unknown; JellyCon v1.0.2 polls segments and seeks; service.skippy v8.33.4 (EDL/XML/online skip, no mute documented) | Lab EDL cut leaked a frame; no exact pass | VERIFIED / lab |
| Fire TV | Android TV app via Amazon; Vega OS needs a separate app in development | n/a | DOCUMENTED |

## Part 3: prior art that filters Jellyfin, Emby or Plex playback

| Project | How it works | State | Tag |
| --- | --- | --- | --- |
| [JellyFilter (jared-skinner)](https://github.com/jared-skinner/jellyfilter/tree/v1.1.2.0) | Per-user, per-category JSON sidecars; server polls reported position (400 ms default) then sends Seek and generic Mute/Unmute to clients ([source](https://github.com/jared-skinner/jellyfilter/blob/v1.1.2.0/Jellyfin.Plugin.JellyFilter/Services/PlaybackFilterService.cs#L339-L445)) | v1.1.2.0, 2026-09-27 | VERIFIED |
| [CorrMedia](https://corrmedia.github.io/) / [plugin v1.5.0.0](https://github.com/CorrMedia/jellyfin-plugin-corrmedia/releases/tag/v1.5.0.0) | Server-first: `.corr.json` mute/cover/skip applied in a temporary transcoded stream, no stored media; needs a patched Jellyfin 12.4; sidecar editor [corrcut](https://github.com/CorrMedia/corrcut/releases/tag/v0.1.2) | 2026-11-16 | DOCUMENTED |
| [jellyfilter (mattbloomfield)](https://github.com/mattbloomfield/jellyfilter/tree/f19a98bfb1d62bdfd116d1e9138f5e65e0884a8a) | Whisper + NudeNet sidecar; forced transcode with an FFmpeg wrapper; skip not implemented | 2026-11-02, no release | DOCUMENTED |
| [Angelfin](https://github.com/chiappone/Angelfin/tree/7e8f7a275b4b036774c2cc940eb4f249c165ef0d) | Native skip by relabelling the segment type; per-user REST; injected Web mute; imports VideoSkip | 2026-06-09, no release | DOCUMENTED |
| [MovieContentFilter plugin (jacob-willden)](https://github.com/jacob-willden/jellyfin-plugin-moviecontentfilter), [Kodi script](https://github.com/jacob-willden/script.movie.content.filter) | Early | no release | DOCUMENTED |
| [SkipMedia](https://github.com/EricAsMyself/jellyfin-plugin-SkipMedia/releases/tag/v.1.0.0.1) | EDL action 3, timer then seek, no mute; tested on 10.10.7 only | 2025-07-05 | DOCUMENTED |
| [VideoSkip](https://github.com/fruiz500/VideoSkip/tree/c3e5503c0a0ad897f3b76c7e073117ec247c9a08) | Browser HTML5 skip/mute/blank with `.skp` files; public exchange with 2026 uploads; licensing and matching unverified | active | DOCUMENTED |
| [MovieContentFilter (delight-im)](https://github.com/delight-im/MovieContentFilter/tree/ab7faf7941d6d993004b4ac6330b14241f4b5978) | Open tag standard | last push 2020 | DOCUMENTED |
| [PlexAutoSkip](https://github.com/mdhiggins/PlexAutoSkip/tree/abcbf0010874c0faf0aa969ae5f96699035ce802) | Real-time markers; seek or remote setVolume on LAN Plex Companion clients | maintenance only | DOCUMENTED |
| [Emby.PottyMouth](https://github.com/BillOatmanWork/Emby.PottyMouth/releases/tag/V1.2.3.0), ComSkipper, ContentCensor | `.edl` mute/skip by remote command; maintainer says results depend on latency | active | DOCUMENTED |
| [Intro Skipper 12.0/v12.0.7.0](https://github.com/intro-skipper/intro-skipper/releases/tag/12.0%2Fv12.0.7.0), TheIntroDB, skipme.db, SponsorBlock plugins | Supply native segments of the standard types; model for broad client adoption, not for exact filtering | active | DOCUMENTED |

None of these is exact under the owner's rule: they either react to a reported position after the fact or work in a server transcode.

## Not verified

Runtime behaviour of every client listed; Infuse acting on Unknown; whether #3396 or #17808 will get maintainer attention; licensing of the VideoSkip exchange; the GitHub project boards (token lacked scope).
