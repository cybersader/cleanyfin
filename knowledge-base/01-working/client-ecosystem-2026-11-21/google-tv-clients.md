# Jellyfin-capable clients on Google TV / Android TV: who could carry exact filtering

Research notes, 2026-11-21. Source reading and public pages only, except the hands-on Wholphin test recorded near the end. Tags: VERIFIED-FROM-SOURCE (pinned source), DOCUMENTED (public page), INFERRED. Activity tuples are GitHub PRs from 2026-05-21 to 2026-11-21: open / closed / merged.

Owner's constraint: no Cleanyfin-owned client; no stored modified media; any marked picture or sound shown is a failure.

**Answer: maybe.** Two existing routes plausibly avoid both modified media and a Cleanyfin-owned client. Neither is proven.

## Shortlist

1. **Wholphin with its built-in mpv** (route a: configuration only). v1.1.2 depends on wholphin-extensions 0.2.4, which builds libmpv [with Lua](https://github.com/damontecres/wholphin-extensions/blob/v0.2.4/wholphin-mpv/src/native/scripts/mpv.sh#L15-L23), sets `config=yes` with the app files dir as config dir, and forwards the raw URI to libmpv [`loadfile`](https://github.com/damontecres/wholphin-extensions/blob/v0.2.4/wholphin-mpv/src/main/java/com/github/damontecres/wholphin/mpv/MpvPlayer.kt#L844-L859). Wholphin uses `source.path` directly for direct-play remote items ([source](https://github.com/damontecres/Wholphin/blob/v1.1.2/app/src/main/java/com/github/damontecres/wholphin/ui/playback/PlaybackViewModel.kt#L709-L729)) and its settings UI [writes that mpv.conf](https://github.com/damontecres/Wholphin/blob/v1.1.2/app/src/main/java/com/github/damontecres/wholphin/ui/preferences/ComposablePreference.kt#L70-L99). VERIFIED. So a library item whose remote URL is a text mpv EDL (kept ranges of the original, same-origin source URLs) could reach libmpv with no edited media anywhere. Its own segment handling is a 500 ms poll that ignores Unknown and then seeks, so the EDL is the route, not its skip feature. Smallest test: does Jellyfin advertise the descriptor as direct-play remote, does Wholphin open it on the mpv backend, and is there no fallback to the unfiltered original. mpv permits remote-to-network EDL references but not remote-to-local ([stream.c](https://github.com/mpv-player/mpv/blob/v0.40.0/stream/stream.c#L218-L239)); do not disable that check.
2. **Stock Jellyfin Android TV app handing off to mpv-android with one trusted local Lua script** (route a/c). Users report `scripts=` config working on a Chromecast ([issue comment](https://github.com/mpv-android/mpv-android/issues/1048#issuecomment-3686088152), DOCUMENTED). mpv's synchronous [`on_load` hook](https://github.com/mpv-player/mpv/blob/v0.40.0/DOCS/man/input.rst#L1861-L1889) can rewrite `stream-open-filename` to a kept-range EDL before demuxing. The stock hand-off cannot itself pass `edl://` or options ([MPVActivity](https://github.com/mpv-android/mpv-android/blob/2026-09-17/app/src/main/java/is/xyz/mpv/MPVActivity.kt#L1106-L1178)). Smallest test: launch one synthetic item from the stock app with a fixed local plan; measure cut and PCM mute boundaries, seek and resume.
3. Secondary: **Streamyfin** (real `config=yes` and config dir, URL to `loadfile`; Lua and TV file provisioning unverified), **JellyPlay** (accepts a real `mpv.conf` and raw extra config), **mpvNova** (TV-first mpv player, builds Lua, editable mpv.conf; no Jellyfin pairing evidence).

Upstream conversation most worth joining: [Wholphin #1527](https://github.com/damontecres/Wholphin/pull/1527), the maintainer's active `.strm` work. It concerns probing remote `.strm` metadata, not filtering; the ask would be narrow: can a metadata-only mpv descriptor be played with no transcode and no unfiltered fallback. Jellyfin's LLM policy applies to the official repos; check each third-party project's own contribution rules before posting.

## Comparison

| Client | Engine on TV | Maintained (release; PR activity; licence) | Segments today | Route | Notes |
| --- | --- | --- | --- | --- | --- |
| Official jellyfin-androidtv | Media3 (legacy player default) | v0.20.0 2026-11-10; 51/205/151; GPL-2.0 | Five types, late seek, ignores Unknown | b or d | Rewrite hidden unless dev/already enabled; its segment service is wired for [Intro only](https://github.com/jellyfin/jellyfin-androidtv/blob/v0.20.0/app/src/main/java/org/jellyfin/androidtv/di/PlaybackModule.kt#L93-L101); one regular MediaItem; no dated plan to make it default. PR #4567 (profanity filter) closed. #5452 preloads the next item only. |
| Wholphin | libmpv (Lua) or Media3 | v1.1.2 2026-11-20; 50/265/224; GPL-2.0 | 500 ms poll, ignores Unknown, seeks | **a** | See shortlist. |
| Streamyfin (TV build, sideload) | libmpv default, Media3 option | v0.55.2 2026-10-16; 73/696/548; MPL-2.0 | Standard types, tick-driven seek | a? / b | config dir real; no user script/EDL hook seen. |
| JellyPlay | libmpv / Media3 / VLC | v0.11.15 2026-11-19; 1/131/124; GPL-3.0 | n/a | a? | Real `mpv.conf` plus free-form options before init. |
| Plezy | MPV / Media3 | 2.26.0 2026-11-09; 61/168/76; GPL-3.0 | n/a | b | Open request for scripts; owner: "Will look into this in more detail soon" ([comment](https://github.com/edde746/plezy/issues/782#issuecomment-4320247361)). |
| Moonfin | Media3 or "mpv legacy" | 3.1.1 2026-11-19; 18/868/785; GPL-3.0 | n/a | b | Custom mpv config [blocks `script`, `scripts`, `load-scripts`](https://github.com/Moonfin-Client/Moonfin-Core/blob/3.1.1/lib/playback/media_kit_player_backend.dart#L2276-L2285). |
| Glacier | Media3 | v0.10.1 2026-11-18; 0/32/18; GPL-3.0 | Timed segment still seeks | b | Already does clipped, prepared Media3 hand-overs for quality switches (not filtering): the closest existing code to an exact Media3 cut. |
| Fladder | Media3 forced on Leanback | v0.12.1 2026-11-20; 27/57/45; GPL-3.0 | n/a | b | libmpv wrapper is not the TV path. |
| Findroid | Media3 / mpv | v1.2.0 2026-10-25; 20/81/63; GPL-3.0 | Native segments, skip button/auto | b | No stable TV APK; TV issue #927 open. |
| mpv-android (external player) | libmpv | 2026-09-17; 11/33/14; MIT | none | a/c with stock app hand-off | See shortlist item 2. |
| mpvNova (external player) | libmpv (Lua) | 2026-11-21; MIT; sideload | none | a/c | Intent excludes `edl://`. |
| Void TV | Exo / libmpv | v0.6.0 2026-04-24; idle; GPL-3.0 | n/a | d | Inactive since April. |
| Kodi + jellyfin-kodi / JellyCon / Skippy | Kodi VideoPlayer | Kodi 21.3, 22.0rc2 2026-11-10; add-ons active | Poll then `seekTime` | a (EDL) | Lab EDL cut leaked a marked frame; not exact. |
| Litefin, Kofin, AFinity, Elefin, Ghosten, Cinetry, Shoumei, Just Player, NOVA, VLC, AfuseKt | mostly Media3 | mixed | n/a | none found | Shoumei builds mpv with Lua disabled. No evidence of a usable hook. |
| jheilos/jellyfin-androidtv-mpv, DUNE | n/a | dormant / archived | n/a | none | Not candidates. |

## Hands-on test, 2026-11-21: Wholphin v1.1.2 and a `.strm` pointing at an mpv EDL

Google TV emulator (`cleanyfin-test`, Android 16, arm64, headless), disposable Jellyfin 10.11.11, unmodified `Wholphin-arm64-v8a.apk` (SHA-256 `5c1cec6cae94614fb7e561017bfb73adc8c621390ea31492893bb6307cff7144`, GPL-2.0). No edited media was created.

**Result: this route did not work as tested.** The cut list never reached libmpv.

- Wholphin's MPV backend works and honours its app-local `mpv.conf` (`audio=no` deselected the audio track). On this headless emulator mpv draws only with `hwdec=no`, `vo=gpu`, `gpu-context=android`, `opengl-es=yes`; with that, the Original control showed marked frames (65 marked barcodes in a 14 fps recording).
- For the `.strm` item whose URL is the EDL text, Jellyfin reports `Protocol=Http`, `IsRemote=true`, `Container=strm`, `SupportsDirectPlay=true`, no runtime and **zero media streams** (the server cannot probe an EDL as media). Wholphin logged `Playback decision ...: Transcode`, asked Jellyfin for `live.m3u8`, and got `HTTP error 500`. No client request for `cut.edl` or the source was observed, and no unfiltered fallback.
- Alternatives tried: letting the server reach the URL (ffmpeg: `Invalid data found when processing input`), serving the EDL text under an `.mp4` name (same), and an inline `edl://` in the `.strm` (Jellyfin: `Ignoring invalid or non-remote .strm path`). All failed before playback.
- `ao=pcm` in `mpv.conf` did not take effect (log still showed `AO: [audiotrack]`), so audio could not be captured this way.

Reading (INFERRED): the blocker is not mpv or the EDL but the item's missing stream metadata, which makes the client choose transcoding. Untested ways around it that keep Cleanyfin out of the client business: (1) the Cleanyfin server plugin supplies a media source for the item with the original's stream metadata and the EDL as its path, so the client chooses direct play; (2) a trusted local mpv script in the client's config directory rewrites the open URL to the kept-range EDL; (3) the Wholphin maintainer's remote-`.strm` work (#1527) changes how such items are probed. None of these has been tried.

## Follow-up test, 2026-11-21: same item, but with the original's stream metadata

Same emulator, lab and unmodified Wholphin 1.1.2 (mpv backend; `mpv.conf` set to `hwdec=no`, `vo=gpu`, `gpu-context=android`, `opengl-es=yes` so the headless emulator draws). To imitate what a server plugin could supply, the lab file server answered one URL two ways: the Jellyfin server's probe (`Lavf/61.7.103`) received the original MP4, the client (`libmpv`) received the EDL text, whose two entries point at a second URL that always returns the original. No edited video existed anywhere.

**Result: with stream metadata present, Wholphin played the cut list directly and the cut was applied.**

- PlaybackInfo advertised a 20 s MP4 with H.264/AAC. Wholphin logged `Playback decision ...: DirectPlay` (it had logged `Transcode` when the item had zero streams). libmpv fetched the EDL text, then the original (`bytes=0-`, HTTP 206). No server transcode, no mpv safety refusal, no unfiltered fallback.
- Picture: control showed 65 marked barcodes in the screen recording and red in 8 of 44 screencaps. The cut item showed zero marked barcodes (recording at 15 fps) and zero red in 48 screencaps (2 Hz). Readable frames spanned 14.93 s, consistent with the 14.96 s filtered timeline.
- Seeking: D-pad forward landed on source frame 301 (7.00 s on the filtered clock), back landed on frame 50; no marked frame in either. The player bar followed about 15 s, not the advertised 20 s. Jellyfin's live session showed the filtered-clock position.

Limits: the 15 fps recording missed nine kept frames near the join in the cut run and nine in the control as well, so loss of kept material at the join is not measured here; sound was not measured; mpv downloaded the whole original including the marked bytes (it presents only the kept ranges; whether it decoded the rest internally is unknown); no resume bookmark was stored (the lab's 300 s minimum applied), so resume mapping is untested; mpv's own demuxer log could not be captured. Confirming the workaround does not prove missing streams were the only cause of the earlier transcode decision. Emulator only.

What this implies for design (INFERRED): a Cleanyfin server component that (1) exposes a filtered item whose media source carries the original's stream metadata and (2) serves a per-item text cut list to the player would let an unmodified, third-party-maintained mpv client play a cut timeline with no edited media stored or streamed. Open points: how a real plugin supplies that metadata (media source provider, or a `.strm` plus stored media info), authentication for the two URLs, the 20 s vs 15 s runtime mismatch, resume and watched-state mapping, sound at the join on Android, and other mpv-based clients (Streamyfin, JellyPlay).

## Not verified

Whether Jellyfin will advertise an EDL descriptor as direct play; script provisioning on a production TV; accuracy on the physical onn. 4K Pro; source-time resume mapping; audio at the joins on Android; any maintainer's willingness to support filtering.
