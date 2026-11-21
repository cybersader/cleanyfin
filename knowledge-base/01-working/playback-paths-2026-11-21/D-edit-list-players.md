# Path D: players that already understand edit lists (Kodi, mpv) plus prior art

Research only, 2026-11-21. Nothing was run or installed; no household device or address was contacted. Tags: **VERIFIED-FROM-SOURCE** (read in the pinned file), **DOCUMENTED** (docs, forum, release notes, web page), **INFERRED** (my reasoning, needs a lab test). Line numbers refer to the file at the pinned tag.

## Verdict first

Path D is a real but narrow route, not a fast one for the first household target.

1. **Kodi cannot get a runtime edit list for a Jellyfin HTTP stream.** Kodi reads a sidecar `.edl` only when the media path is local or on a LAN share and is not an "internet stream". `http`/`https` are internet streams. The only other source is PVR recordings/EPG items. So Kodi needs the native-path mode of jellyfin-kodi (smb/nfs/local paths) and a writable place beside each file. See section 1.
2. **Kodi's cut is better designed than Jellyfin's native skip, but not proven.** Packets inside a cut are dropped at the demuxer before decode, then a seek follows. Mute drops whole audio packets, so it is packet-granular, not sample-exact. Both need a measured lab test against the owner's "any detected marked picture or sound is a failure" rule.
3. **mpv is the better engine on paper.** Its EDL timeline clips packets and audio samples at segment boundaries inside the decoder wrapper, so cut should be close to exact. mpv EDL cannot mute, so mute needs an audio filter or a second audio timeline. mpv-android runs on Android TV and the stock Android TV app 0.20.0 can hand a plain stream URL to it, but it passes no segment data, so a player-side script or proxy would have to look the plan up itself.
4. **The path is a different client, not a plugin.** On Google TV the family would have to move from the stock Jellyfin app to Kodi (with jellyfin-kodi) or to mpv-android launched from Jellyfin, plus the household would need a way to deliver a per-item plan. That is the same "change the client" cost path D was meant to avoid, only smaller on the filtering side.

## 1. Kodi (xbmc 21.3-Omega)

Sources: [Edl.cpp](https://github.com/xbmc/xbmc/blob/21.3-Omega/xbmc/cores/VideoPlayer/Edl.cpp), [VideoPlayer.cpp](https://github.com/xbmc/xbmc/blob/21.3-Omega/xbmc/cores/VideoPlayer/VideoPlayer.cpp), [URIUtils.cpp](https://github.com/xbmc/xbmc/blob/21.3-Omega/xbmc/utils/URIUtils.cpp), [PVREdl.cpp](https://github.com/xbmc/xbmc/blob/21.3-Omega/xbmc/pvr/PVREdl.cpp). `Edl.cpp` is byte-identical at 21.2-Omega; `VideoPlayer.cpp` differs by 31 diff lines, none mentioning edl, cut or mute. The Play Store build was 21.2 as of the sources below, so the findings apply to both.

| Question | Finding | Tag |
|---|---|---|
| Where the file must live | `ReadEditDecisionLists` (Edl.cpp:46-88, gate at line 55-56) reads sidecars only if `IsHD(path) \|\| IsOnLAN(path, ANY_PRIVATE_SUBNET)` **and** `!IsInternetStream(path)`. Filename is the media path with the extension swapped to `.edl` (line 95). Order tried: VideoReDo, `.edl`, comskip, BeyondTV. | VERIFIED-FROM-SOURCE |
| HTTP streams | `IsInternetStream` returns true for `http` and `https` (URIUtils.cpp:1052). Those fall to `ReadPvr` (Edl.cpp:78-79), which reads edits only from a PVR recording or EPG tag (PVREdl.cpp:20-38). A Jellyfin direct-stream URL has neither. **No runtime edit-list path exists for a Jellyfin HTTP stream.** A plain add-on cannot inject cuts. | VERIFIED-FROM-SOURCE |
| `dav://` or `smb://`/`nfs://` | `dav` is absent from the internet-stream protocol list, so a WebDAV path would probably pass the gate, and smb/nfs pass as LAN shares. I did not read `IsRemote`/`IsHostOnLAN` fully for `dav`. | INFERRED |
| Cut (action 0) timing | Two mechanisms. (a) Demux-time drop: video and audio packets whose `dts + offset` is inside a cut are dropped before they reach the decoder queue (VideoPlayer.cpp:1700-1710 audio, 1734 video, check at 2374-2388). (b) Seek: the main loop calls `CheckAutoSceneSkip` (line 1383, body 2391-2446). When playback clock is inside a cut and within 1000 ms of its start, it queues an `accurate` backward-flag seek to the cut end, `sync=true`. So a cut is a drop plus a seek after the clock enters, not a pre-built timeline. | VERIFIED-FROM-SOURCE |
| Marked-picture exposure | Packet drop is by **dts**, in integer milliseconds, inclusive at both ends. With B-frames a packet whose dts is just before the cut can have a pts inside it. That could present up to a reorder-depth of marked frames (guess: 2-4 frames). Also unknown: whether the last pre-cut frame freezes while the seek completes. Needs measurement. | INFERRED |
| Mute (action 1) timing | Audio packets with `dts` inside `[start, end]` are dropped (VideoPlayer.cpp:1705-1708). It is not a volume change. Granularity is one audio packet (about 21 ms for 1024-sample AAC at 48 kHz, 32 ms for AC-3). A packet that starts just before the mark but overlaps it would play; one overlapping the end is lost. That is a possible failure under the owner's rule and unverified. | VERIFIED (mechanism) / INFERRED (audible consequence) |
| Mute sync reports | Forum users report audio sync trouble for seconds around mutes since 17.x and a mute that arrives about a second late; one says "EDL broken since 17.x". Old threads, anecdotal, no matching GitHub issue found. [18.5 thread](https://forum.kodi.tv/showthread.php?tid=349685&page=2), [Mute Profanity thread](https://forum.kodi.tv/showthread.php?tid=169029&page=11) | DOCUMENTED (forum) |
| "Cut shows a flash" reports | None found in web search. Absence of reports is not evidence of exactness. | DOCUMENTED (absence) |
| Autowait / autowind | `m_iEdlCommBreakAutowait/Autowind` apply only inside the `COMM_BREAK` branch of `AddEdit` (Edl.cpp:652-675). Cuts and mutes get **no margin**, which matches the "no trim margin approved" rule. | VERIFIED-FROM-SOURCE |
| Frame vs time | Fields accept seconds with decimals, `HH:MM:SS.sss`, or `#frame` (frame form needs a known fps, lines 150-215). Storage is integer ms. Overlapping/enclosing edits are rejected (`AddEdit`, 603-704), as is start >= end. | VERIFIED-FROM-SOURCE |
| Seek into a cut | Resume or seek target inside a cut is moved to the cut end (`GetTimeAfterRestoringCuts`, VideoPlayer.cpp:1289-1293; start inside first cut, 1299-1309). Seeking backward into a cut goes to the cut start (line 2431). Displayed time and duration subtract cut time (`GetTimeWithoutCuts`, `GetTotalCutTime`, VideoPlayer.cpp:4931-4934), so the progress bar differs from source time. | VERIFIED-FROM-SOURCE |
| jellyfin-kodi 2.2.0 modes | Playback chooses direct play (native path via `API.get_file_path`, with server-path to Kodi-path replacements from the add-on's `paths` data) when `SupportsDirectPlay`, `playFromStream` is off and `xbmcvfs.exists(path)`; otherwise a `/Videos/{id}/stream?static=true&...ApiKey=` URL or transcode (playutils.py:177-232, 306-336; api.py:217-253). So jellyfin-kodi can give Kodi an smb/nfs path, which is the only mode where an `.edl` is readable. | VERIFIED-FROM-SOURCE |
| jellyfin-kodi segments | `segments.py` polls once per second (`xbmc.sleep(1000)`, `int(player.getTime())`) and maps only the five known types; `Unknown` is dropped by test (`tests/test_media_segments.py` line 47-76). This add-on path is too coarse and too late. | VERIFIED-FROM-SOURCE |
| service.jellyskip | Skip-button service using Media Segments; last push 2025-11-18, 30 stars. A skip prompt, not an edit engine. [repo](https://github.com/SgtJalau/service.jellyskip) | DOCUMENTED |
| Kodi on Google TV | In the Play Store for Android TV/Google TV (Kodi Foundation), but the store build was 21.2 while 21.3 was sideload-only because of Google's 16 KB page-size requirement; remote-control navigation is first-class. [TroyPoint](https://troypoint.com/update-kodi-on-android/), [androidtvnews](https://androidtvnews.com/install-kodi-on-android-tv/) | DOCUMENTED (third-party sites) |
| jellyfin-plugin-edl | Writes `.edl` beside media, needs a writable library, last release 0.3.0 (2024-09-02 push), README still says Jellyfin 10.10. [repo](https://github.com/endrl/jellyfin-plugin-edl) | VERIFIED (via GitHub API) |

**Reading.** Kodi's edit list is the one tested product code that cuts and mutes timestamps before presentation, and it needs the media on a path Kodi can stat. For a household that already uses Jellyfin, that means switching the TV from stock Jellyfin to Kodi, enabling native paths, and exposing a share Kodi can reach. Mute via packet dropping is the weak point for the owner's rule.

## 2. mpv 0.42.0 and mpv-android

Sources: [edl-mpv.rst](https://github.com/mpv-player/mpv/blob/v0.42.0/DOCS/edl-mpv.rst), [demux_timeline.c](https://github.com/mpv-player/mpv/blob/v0.42.0/demux/demux_timeline.c), [demux_edl.c](https://github.com/mpv-player/mpv/blob/v0.42.0/demux/demux_edl.c), [f_decoder_wrapper.c](https://github.com/mpv-player/mpv/blob/v0.42.0/filters/f_decoder_wrapper.c), [options.rst](https://github.com/mpv-player/mpv/blob/v0.42.0/DOCS/man/options.rst).

| Question | Finding | Tag |
|---|---|---|
| EDL semantics | A virtual file concatenating `<file>,<start>,<length>` source ranges. A cut is expressed by listing the **kept** ranges of the same URL, e.g. `url,0,12.34` then `url,15.60`. First line must be `# mpv EDL v0`; `;` may replace newlines. Per-file `--{ --start --length --}` gives a playlist, not one timeline. mpv documents the EDL format as "not frozen". | VERIFIED-FROM-SOURCE |
| `edl://` input | `edl://[spec]` is accepted as a path (input.rst line 1468). The EDL doc notes that relative paths and protocol prefixes "may be prevented for security reasons". Whether an `http(s)` source inside `edl://` is permitted by default was not tested. | VERIFIED (syntax) / INFERRED (http allowed) |
| Segment start accuracy | `switch_segment` forces `SEEK_HR` (precise seek) when not seeking forward (demux_timeline.c:242-243), then seeks the sub-demuxer to the segment start. The decoder wrapper drops frames with pts below the start pts (f_decoder_wrapper.c:807-808, 998-1011). | VERIFIED-FROM-SOURCE |
| Segment end accuracy | Packets at or past `seg->end` are dropped (demux_timeline.c:284, 352), `pkt->end` is clamped (339-340), video frames with `pts >= end` end the segment (f_decoder_wrapper.c:811-813), and audio frames are trimmed by `mp_aframe_clip_timestamps(aframe, start, end)` (line 822). So audio at a segment edge appears **sample-clipped**, which is better than Kodi's packet drop. Whether this holds in real streams across codecs is untested. | VERIFIED (mechanism) / INFERRED (result) |
| Marked frames shown before cut? | The player builds the timeline, so no playhead "reaches" the cut and then seeks. Marked frames are never in the output timeline. Decoder still decodes from the earlier keyframe, but those frames are dropped before display. | INFERRED from the above |
| Cost of many segments | Each segment is a separate sub-demuxer open/seek, with `reopen_lazy_segments`. Dozens of cuts on a remote HTTP source means dozens of seeks. Performance on a TV SoC is unknown. | INFERRED |
| Mute in EDL | EDL has no mute action. Options: (a) an `aeval`/`volume` filter with expressions on time. ffmpeg's `volume enable=` is evaluated **per frame**, while `aeval` exposes per-sample `t`, so `aeval` is the sample-accurate candidate; (b) a `!new_stream` audio partition built from original-audio ranges plus a generated silence source; (c) a Lua script setting `mute` from a timer. Script timers are polled, so (c) is the least exact. Whether filter `t` stays aligned with source time after EDL assembly and seeks is untested. | INFERRED |
| Lua/JS skip scripts | SponsorBlock ports for mpv exist (e.g. [po5/mpv_sponsorblock](https://github.com/po5/mpv_sponsorblock), last push 2024-05-01). They observe `time-pos` and issue seeks: the same late-seek design as native Jellyfin skip. | DOCUMENTED |
| mute-timing option | `seek ... frame-step mute` mutes only a frame-step; no mpv property is timed to a source timestamp. | VERIFIED-FROM-SOURCE (input.rst 383) |
| mpv-android | [Releases](https://github.com/mpv-android/mpv-android) latest 2026-09-17, active. Manifest declares leanback and touchscreen as **not required**; README: "Android TV is supported, but only available on F-Droid or by installing the APK manually". Lua is compiled in (`-Dlua=enabled`, buildscripts/scripts/mpv.sh). Whether user scripts can be dropped in a config dir on-device was not found; unknown. mpv.conf can be edited in-app. | VERIFIED (manifest, README, build) / UNKNOWN (scripts) |
| mpv-android intent | Extras handled in `parseIntentExtras` (MPVActivity.kt:1143-1175): `subs`, `subs.enable`, `position`, `title`, `decode_mode`, plus an unnamed loop that pushes any other string extra as `file-local-options/<key>`. Result extra `position` is returned (line 338). The extras loop suggests mpv options could be passed by a custom launcher, but the stock Jellyfin app sends none. | VERIFIED-FROM-SOURCE (extras loop line 1149 read; semantics for non-listed keys INFERRED) |
| Stock Android TV 0.20.0 hand-off | External player activity builds `getVideoStreamUrl(itemId, mediaSourceId, static=true)` and launches `ACTION_VIEW` with that URL and MIME type (ExternalPlayerActivity.kt:109-135). `MpvExternalPlayerApi` adds `title`, `position` (ms) and subtitle URLs only. No segments, no plan, no profile. Position is read back. The 0.20 release added the feature. [ExternalPlayData.kt](https://github.com/jellyfin/jellyfin-androidtv/blob/v0.20.0/app/src/main/java/org/jellyfin/androidtv/ui/playback/external/ExternalPlayData.kt), [MpvExternalPlayerApi.kt](https://github.com/jellyfin/jellyfin-androidtv/blob/v0.20.0/app/src/main/java/org/jellyfin/androidtv/ui/playback/external/MpvExternalPlayerApi.kt), [0.20 post](https://jellyfin.org/posts/androidtv-v0.20.0/) | VERIFIED-FROM-SOURCE |
| jellyfin-mpv-shim | Desktop only (Windows, Linux via Flathub/pip, macOS unofficial), v3.3.0 2026-10-23, no segment or skip code found in the file tree. Not usable on a TV box. [repo](https://github.com/jellyfin/jellyfin-mpv-shim) | VERIFIED (README, tree) |

**Reading.** The stock Android TV app is a plausible launcher: the user taps play, the app starts mpv-android with the item's static-stream URL (which carries the item id and media-source id). To filter, something has to turn that URL into an EDL plus an audio mute. Candidates: a player-side script (needs on-device scripting that I could not confirm), a Cleanyfin proxy that rewrites an `edl://`-style response (mpv would need to fetch an `.edl` over HTTP, which mpv treats as a playlist file and may restrict), or a patched mpv-android build. Each is a separate piece of work.

## 3. Other Android TV players

| Player | Evidence | Custom action / mute / scripting | Maintenance | Tag |
|---|---|---|---|---|
| Streamyfin (Android TV APK, mpv inside) | `hooks/useSegmentSkipper.ts` handles Intro/Outro/Recap/Commercial/Preview only, driven by `currentTime` and a seek callback; `modules/mpv-player/...` | Skip/ask only; `Unknown` not mapped; same late-seek design as stock | v0.55.2, 2026-10-16; pushed 2026-11-21 | VERIFIED-FROM-SOURCE |
| Findroid | `FindroidSegmentType` includes `UNKNOWN` but nothing but the model file references it; README requires Jellyfin 12.0+ and lists Android TV | Skip button/auto-skip; no mute | pushed 2026-11-21 | VERIFIED-FROM-SOURCE |
| Stock Jellyfin Android TV 0.20.0 | Per prior docs 10 and spike-c | none | active | already established |
| VLC / Vimu / MX | Only special-cased by the stock app's external-player code for subtitles and position return | No Jellyfin segment hook; VLC has Lua extensions in desktop builds, not verified on Android TV | n/a | INFERRED (not read) |
| Just Player (moneytoo/Player) | ExoPlayer-based, active (pushed 2026-11-12) | No edit-list or script feature found in description; not read in detail | active | DOCUMENTED (repo summary only) |
| Void | No repository found by search | none | n/a | not found |

None of the checked players offers an edit-decision or mute primitive for Jellyfin streams.

## 4. Commercial and open prior art, additions only

`.claude/04-PRIOR-ART.md` and `prior-art-and-oss-competitors.md` already cover the legal story, MCF, cleanvid, PlexAutoSkip's volume mute and TVGuardian's caption approach. Added here:

| System | Side | How exposure is avoided / how mute works | Tag |
|---|---|---|---|
| ClearPlay (DVD-era hardware, USB filter file `.CPF`) | Client-side, own player firmware | Player reads a time code from the disc and, on match with a filter entry, runs a skip (duration given in frames) or mute; filter downloaded to a USB stick, disc matched by title. Because the player owns the decode path and the filter is checked against the time code ahead of presentation, a skip is a navigation action, not a late seek on a generic player. [patent application](https://www.freepatentsonline.com/y2016/0323644.html), [Wikipedia](https://en.wikipedia.org/wiki/ClearPlay) | DOCUMENTED |
| ClearPlay streaming | Client-side browser extension | Filters hold timestamps for specific versions; ad insertion shifts timing and can break filters. Shows the timing-fragility class Cleanyfin also faces. [help article](https://help.clearplay.com/docs/can-i-filter-youtube-video-games-or-other-entertainment) | DOCUMENTED |
| VidAngel | Originally server-side (decrypted copies, ruled infringing); now an overlay on the user's own streaming accounts | Today: client-side skip or mute while playing; vendor pages do not describe frame handling. Technical detail is in patents (e.g. US7543318B2), not read. | DOCUMENTED (marketing level) / not read (patents) |
| Enjoy Movies Your Way | Client-side | Legacy: Windows software storing marks and driving VLC to skip or mute DVDs/local files; current: remote-control-style commands such as "mute at time" or "jump to time" sent to streaming players, per one secondary source. No exposure-avoidance mechanism documented. [legacy page](https://www.enjoymoviesyourway.com/static/enjoy-legacy.htm) | DOCUMENTED (secondary) |
| Plex skip intro | Client-side; markers from `includeMarkers=1`; one open client seeks to `start+2 s` once playback has entered the marker | Late seek; same behavior as Jellyfin native. [MyPlexPlayer PR](https://github.com/biskyt/MyPlexPlayer/pull/31) | DOCUMENTED |
| SponsorBlock clients | Client-side | Browser extension watches `currentTime` and seeks (the mpv port follows the same polling idea). `actionType` mute exists in the API, applied by the extension. | DOCUMENTED (see existing prior-art file) |
| Kodi "content filter" add-ons | Client-side | `script.movie.content.filter` (MCF Kodi add-on) is listed by the existing prior-art file; I did not read its code, so I cannot say if it uses EDL or polling. | not read |

Lesson: players that avoid showing marked frames own the playback pipeline (ClearPlay hardware, or a timeline built before presentation). Polling-and-seek designs (Plex, SponsorBlock, jellyfin-kodi segments, Streamyfin) all show some marked material first. Kodi EDL and mpv EDL timelines are the two open-source engines in the first category, and mpv's looks structurally stronger for cuts.

## 5. Feeding these players from Cleanyfin

| Need | Approach | Tag |
|---|---|---|
| Source plan | Per item and viewer, ask the Cleanyfin household resolver for approved intervals bound to the selected edition (SHA-256), same contract as note 10. Never emit a plan for an unverified source. | INFERRED (design) |
| Kodi `.edl` | Lines `start end action` with action 0 (cut) or 1 (mute); seconds with decimals, millisecond resolution; no overlaps or enclosures, merge in the generator; no margin. | VERIFIED-FROM-SOURCE |
| mpv EDL | Invert the cut list to **kept** ranges. Needs `# mpv EDL v0`, exact `start` and `length`, no extra whitespace (doc: "relatively strict"). Mute list becomes an `aeval` expression or a silence audio partition. | VERIFIED (format) / INFERRED (mute) |
| Writable library | Do not write into the household library. Build a separate **view tree** served by Samba/NFS: symlinks to media plus generated `.edl` beside each link. Kodi then sees `smb://host/cleanyfin-kids/Movie (2020)/Movie.mkv` and finds `Movie.edl`. Samba must follow symlinks. jellyfin-kodi path replacement maps server paths to this tree. | INFERRED |
| Per viewer | Kodi has one file beside each path, so one view tree per distinct profile (or per user), and per-user path mapping in the add-on; a shared TV would use one tree per profile. mpv can build the EDL per launch from the viewer's plan, which is easier. | INFERRED |
| Plan changes mid-play | Kodi reads the `.edl` at open; mpv reads the EDL at open. A revoked or changed plan only applies on the next open. | INFERRED |

## Viability verdict

| Player | Cut | Mute | Notes |
|---|---|---|---|
| Kodi 21.2/21.3 on Google TV | **Conditional.** Demux drop plus accurate seek, no margin applied. Needs native-path mode, a symlink view tree with sidecars, and a measured exposure test (B-frame dts/pts risk). Not viable over Jellyfin HTTP streams. | **Doubtful** for the owner's rule. Packet-granular drop; anecdotal sync problems; needs a measured test. | Biggest cost: switching the family from stock Jellyfin to Kodi and rebuilding library paths. |
| mpv EDL (desktop) | **Likely good.** Timeline built before presentation; audio sample-clipped at edges. Needs frame and sample measurement across codecs and a many-segment performance check. | **Possible, unproven.** `aeval` per-sample gate or a silence partition; both need a lab test. | Best engine to try first. |
| mpv-android launched from stock Android TV 0.20.0 | **Blocked by plan delivery.** Player can run EDLs but gets only a URL, title, position, subtitles. | **Blocked** (same). | Needs a script mechanism, proxy, or patched build. |
| Streamyfin, Findroid, stock Jellyfin clients | No: skip-by-poll, `Unknown` ignored or unmapped. | No mute primitive. | Not path D. |

## Smallest lab prototype (Linux desktop first)

1. Build or reuse the note-10 fixture, plus two harder cases: a mark that starts and ends mid-GOP with a retained-witness frame and tone immediately on each side, and a B-frame H.264 variant. Record SHA-256 and the expected kept ranges.
2. **mpv:** write a small generator (Python or Go) that turns a plan into (a) a Kodi `.edl` and (b) an `edl://` or `.edl` file of kept ranges, plus an `aeval` mute expression. Run `mpv --vo=image`/`--ao=pcm` (or equivalent) against local files and then against an HTTP URL from a throwaway local web server. Test `edl://` with an HTTP source.
3. **Kodi:** a disposable Kodi 21.3 on the desktop with a local file in a library folder and a Samba or loopback share path, sidecar next to a symlink. Play it and capture video and audio from the output (screen capture plus the audio sink monitor).
4. Measure for each engine and each fixture: marked frames seen (count and ms), marked audio heard (any sample above threshold inside the mark), preserved frames and audio on both sides of the mark (must equal the reference), seek into a cut, seek back into a cut, resume inside a cut, time from entering the cut to resumed output, and CPU/startup cost with 1, 20 and 100 cuts.
5. **Kill rule:** if Kodi shows any marked frame or audible marked sound on the mid-GOP fixture, drop Kodi cut/mute for this path. If mpv passes cut but not mute, keep mpv for cut only and decide on mute separately.

Estimated effort: generator plus fixtures about 1 day; mpv measurement about 1 day; Kodi plus Samba view tree about 1-2 days. A Google TV box prototype is a further 3-5 days, mostly packaging and plan delivery.

## Open unknowns

- Whether Kodi's dts-based drop leaks marked frames with B-frames, and whether the clock-driven seek shows a freeze.
- Kodi packet-level mute audibility at boundaries and the late/stuttered-audio reports on 21.x.
- Whether `dav://` passes Kodi's EDL gate and works with a Cleanyfin-hosted share (alternative to Samba).
- mpv: `edl://` with HTTP sources, per-segment cost over HTTP, audio `t` alignment for `aeval` after EDL assembly, and mute through a silence partition.
- mpv-android: whether user scripts or option files can be placed on Google TV, and whether intent extras can carry options for an EDL from a non-stock launcher.
- Whether the family accepts running Kodi or mpv-android instead of stock Jellyfin on the onn. 4K Pro, and how unsupported or expired plans should fail (owner decision).
- Patent text for VidAngel/ClearPlay mechanisms was not read; ClearPlay details are from the patent abstract and press, secondary.
- Void could not be identified.
- WebFetch of the mpv-android intent doc was blocked by an API safeguard error, so intent details come from source only.
