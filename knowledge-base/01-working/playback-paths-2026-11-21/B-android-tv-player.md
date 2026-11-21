# Path B: a filtering-capable native player on the onn. 4K Pro

Research pass, 2026-11-21. No code was run, nothing installed, no emulator, no household device contacted. Evidence tags: **VERIFIED-FROM-SOURCE** (read at a pinned tag), **DOCUMENTED** (vendor docs, issue/PR pages, web pages), **INFERRED** (my reasoning, not tested).

Pinned sources: `jellyfin/jellyfin-androidtv` tag `v0.20.0` (released 2026-11-10; the release has a signed APK asset) and `androidx/media` tag `1.11.1` (the exact Media3 version in the app's `gradle/libs.versions.toml`: `androidx-media3 = "1.11.1"`, `minSdk 23`, `compileSdk 37`, `jellyfin-sdk 1.8.12`). Base URL for app links: `https://github.com/jellyfin/jellyfin-androidtv/blob/v0.20.0/`. Media3 base: `https://github.com/androidx/media/blob/1.11.1/libraries/exoplayer/src/main/java/androidx/media3/exoplayer/`.

## 1. How the app plays video at v0.20.0

There are two video stacks, and the default one is the old one.

| Fact | Tag | Source |
| --- | --- | --- |
| `PlaybackLauncher` picks external player if `useExternalPlayer`, else the "rewrite" player if `playbackRewriteVideoEnabled`, else the legacy player. | VERIFIED-FROM-SOURCE | `app/.../ui/playback/PlaybackLauncher.kt` |
| `playbackRewriteVideoEnabled = booleanPreference("playback_new", false)` and `useExternalPlayer` defaults to `false`. So a stock household install runs the **legacy** `PlaybackController` + `VideoManager`. | VERIFIED-FROM-SOURCE | `app/.../preference/UserPreferences.kt` lines 94, 197 |
| Legacy path: `PlaybackController.startItem` calls `mVideoManager.setMediaStreamInfo(api, response)` (PlaybackController.java:690), then `applyMediaSegments` (line 693). `VideoManager.setMediaStreamInfo` builds **one** `MediaItem` from `streamInfo.getMediaUrl()` with external subtitle configs and calls `setMediaItem` + `prepare` (about VideoManager.java lines 417-423). No clipping, no concatenation. | VERIFIED-FROM-SOURCE | `ui/playback/PlaybackController.java`, `VideoManager.java` |
| Legacy segment handling: `getSegmentsForItem` queries only `MediaSegmentRepository.SupportedTypes` (Intro, Outro, Preview, Recap, Commercial). `addSkipAction` uses `ExoPlayer.createMessage` at `segment.start` (min 1 ms), and on delivery posts `seek(segment.end)` through `PlaybackController.seek`. | VERIFIED-FROM-SOURCE | `PlaybackControllerHelper.kt` `applyMediaSegments`, `addSkipAction`; `MediaSegmentRepository.kt` |
| `PlaybackController.seek` rebuilds the stream if the item is not seekable (transcode fallback) and sets `mSeekPosition`. Positions are always source-timeline milliseconds. | VERIFIED-FROM-SOURCE | `PlaybackController.java` lines 965-1030 |
| Rewrite stack: module `playback/core` (`PlaybackManager`, `QueueEntry`, `TimedEvent`, `TimedEventService`), `playback/jellyfin` (`MediaSegmentService`, queue suppliers), `playback/media3/exoplayer` (`ExoPlayerBackend`). `ExoPlayerBackend.prepareItem` creates a `MediaItem` with `setUri(stream.url)` and the `QueueEntry` as tag (line 231-240), keeps up to 10 items in the ExoPlayer playlist (`MEDIA_ITEM_COUNT_MAX = 10`), `setPauseAtEndOfMediaItems(true)`, and offload enabled in the track selector (line 129). `DefaultMediaSourceFactory` decides progressive vs HLS from the URI. | VERIFIED-FROM-SOURCE | `ExoPlayerBackend.kt` |
| Rewrite `TimedEvent` API has `Instant` and `Block` (start/end, `onActivate`, `onDeactivate`, with `BlockActivation.Natural` or `.Seek`). Implemented with `createMessage(...).setPosition(ms)`; `onPositionDiscontinuity` drives seek-in/out. `MediaSegmentService.asTimedEvents` seeks to `endTicks` only on `Natural` activation (line 79). It calls `getItemSegments(baseItem.id)` with no type filter, then keeps only `skipTypes` passed into `JellyfinPlugin`; I did not trace where `skipTypes` is built. | VERIFIED-FROM-SOURCE | `TimedEventState.kt`, `MediaSegmentService.kt` |
| Direct play vs transcode: the media URL comes from the server's `StreamInfo`. Direct play of a file is a progressive URL; transcode/remux is HLS. The legacy controller restarts the stream at a new `StartTimeTicks` on seek when not seekable. | VERIFIED-FROM-SOURCE (URL from `getMediaUrl`), INFERRED (that direct play is progressive and transcode HLS; not traced through the SDK profile code) | `VideoManager.java`, `PlaybackController.java` |

**Where a filtered timeline could be inserted.**

- Legacy path: one replacement point, `VideoManager.setMediaStreamInfo`. Build a `MediaSource` (or playlist of clipped `MediaItem`s) instead of the single `MediaItem`. Everything that reads position (`getCurrentPosition`, seek bar, progress reports, resume) then sees an edited timeline and needs a source-time <-> edited-time map. (INFERRED)
- Rewrite path: `ExoPlayerBackend.prepareItem`, same idea, and `QueueEntry` already carries per-item elements where a plan can live. Because `ExoPlayerBackend` already holds an ExoPlayer *playlist* of up to 10 items, it is the more natural host, but it is a non-default preference ("playback_new") that upstream is still migrating towards (PR #5440 text: the timed-event API is the foundation "for other functionality like preloading (gapless playback)"). (VERIFIED-FROM-SOURCE for the code; DOCUMENTED for the PR text, https://github.com/jellyfin/jellyfin-androidtv/pull/5440)

## 2. Media3 1.11.1 mechanisms and their real accuracy

### 2.1 Clipping and joins

| Question | Finding | Tag |
| --- | --- | --- |
| Is there a first-class way to play only source ranges? | `MediaItem.ClippingConfiguration` (start/end position, `startsAtKeyFrame`) wrapped by `ClippingMediaSource`; several clipped items can be played as playlist items or joined with `ConcatenatingMediaSource2` (single window, one timeline). `ConcatenatingMediaSource2` requires the per-source window offset not to change during its lifetime. | VERIFIED-FROM-SOURCE (`MediaItem.java`, `ConcatenatingMediaSource2.java` header) |
| Does a clip start decode from the previous keyframe and drop earlier frames? | Yes by design. `ClippingMediaPeriod.shouldKeepInitialDiscontinuity` (lines 353-380) keeps an initial discontinuity "to ensure renderers decode-only the samples before the requested start position". `MediaCodecRenderer` marks an output buffer decode-only when `presentationTimeUs < getLastResetPositionUs()` (MediaCodecRenderer.java:2314-2317), and `MediaCodecVideoRenderer`/`MediaCodecAudioRenderer` then release it without rendering (video 2934, audio 916). So the **start edge is frame-accurate for video** and drops audio at **decoder-output-buffer** granularity. | VERIFIED-FROM-SOURCE |
| `startsAtKeyFrame`? | Javadoc: if false, "the playback transition into the clip may not be seamless (default false)". Keep false for arbitrary cut points; true only if the start is known to be a keyframe. | VERIFIED-FROM-SOURCE |
| Does the end edge cut exactly? | For progressive and HLS the period accepts `setEndPositionUs` (ProgressiveMediaPeriod.java:642, HlsMediaPeriod.java:492), so `ClippingMediaPeriod` sets `isPeriodClippingEndPosition` and passes samples through; the stream carries `FLAG_STRICT_DURATION`, and `MediaCodecRenderer` marks output buffers with `pts - streamOffset >= durationUs` decode-only (MediaCodecRenderer.java:2310-2313). **Video frames at/after the end are not shown (frame-exact). Audio is dropped per decoder output buffer by timestamp, with no sample-level truncation I could find** (grep for strict-duration handling found it only in `MediaCodecRenderer`, none in `MediaCodecAudioRenderer`/`DefaultAudioSink`). An audio buffer that starts before the cut point and extends over it will play in full. | VERIFIED-FROM-SOURCE for the mechanism; INFERRED for the consequence (up to one decoder output buffer, e.g. one AAC frame of 1024 samples = 21.3 ms at 48 kHz, plays inside the mark). The failure rule in this project makes that a fail unless cut points fall on audio-frame boundaries or the audio is edited as PCM (2.3). |
| Audio at the start edge | `ClippingMediaPeriod.updateDecoderDelayPaddingForClipping` clears encoder delay/padding when start != 0 or end set. Decode-only audio buffers whose timestamp is < start are discarded whole, so a frame straddling the start loses the part after the start (preserved material lost, no exposure). | VERIFIED-FROM-SOURCE, consequence INFERRED |
| B-frames at the end edge | The EOS-by-timestamp path in `ClippingSampleStream.readData` (lines 446-453) is skipped when the period itself clips; if a source ever used that path, decode-order vs presentation-order could drop allowed B-frames. For progressive/HLS the strict-duration path works on output pts, so this should not occur. | INFERRED |
| Is the join gapless? | Docs claim "Transitions between items in a playlist are seamless" (https://developer.android.com/media/media3/exoplayer/playlists), but that is not a measured frame-level guarantee. A clip start with `startsAtKeyFrame=false` signals a discontinuity and renderers are reset; video re-init cost and any black/held frame at the join are **unmeasured**. `ConcatenatingMediaSource2` (single window) is the right container if the seek bar must be one continuous edited timeline. | DOCUMENTED (claim), INFERRED (risk) |
| Positions, durations | Edited timeline duration = sum of clips. Seek position, `createMessage` positions, progress reports, resume points would all be in edited time unless mapped. | INFERRED |
| Same container re-opened many times | Each clip is a separate `ClippingMediaSource` over the same URL. For direct play (HTTP range requests) each clip opens its own extractor and seek-to-keyframe lookup; for matroska/mp4 with good seek index this is cheap, for TS/poor index it costs more. Server transcode (HLS) clips would each be a separate transcode session, which is likely unworkable. | INFERRED |

### 2.2 `Player.createMessage` at a position

`createMessage(...).setPosition(ms)` delivers on the playback thread at the time the playback position crosses the position, then the app posts to a handler (the app posts `seek` through `lifecycleScope.launch(Dispatchers.Main)`). The resulting seek is asynchronous, so marked frames can render before the seek completes. This matches the lab result in `08-jellyfin-web-skip-lab.md` (4 red frames before the jump in Web) and is the reason the stock skip action is not a filter. The Android TV code has the same shape (message -> main-thread seek, VERIFIED-FROM-SOURCE), but **I have no measurement of how many frames Android TV leaks**; Media3 messages are delivered on the playback thread near the position, so leakage is likely smaller than a web timer but not zero. (INFERRED.) The messages also never fire for seeks into a block (the app only handles `Natural`, line 79 of `MediaSegmentService.kt`; legacy path has no seek-in handling at all).

### 2.3 Mute: volume vs `AudioProcessor`

| Approach | Behavior | Tag |
| --- | --- | --- |
| `player.volume = 0` from a `createMessage` | Control-thread latency plus the audio sink buffer (AudioTrack buffer is typically tens to hundreds of ms ahead of the displayed position): muting at position T mutes audio that is already queued only after the track's buffer drains, so mute lands late relative to the picture, and unmute also lands late. Not sample-accurate. | INFERRED |
| Custom `AudioProcessor` in `DefaultAudioSink` (via `DefaultAudioProcessorChain` or `AudioProcessorChain`) that zeroes samples inside a list of presentation-time ranges | Sample-accurate relative to the PCM stream because it operates on the exact PCM samples before the AudioTrack write. Needs a way to know the stream's presentation time of each buffer (track samples-in count since the last flush, as `SilenceSkippingAudioProcessor`/`TrimmingAudioProcessor` do); seeks and flushes reset it, so the processor must be told the new base position. Must be added through a custom `AudioSink` or renderers factory in the fork. | INFERRED (design); VERIFIED-FROM-SOURCE that `DefaultAudioSink` runs an `AudioProcessingPipeline` for PCM input (DefaultAudioSink.java ~751-765) |
| **Passthrough (Dolby AC-3/E-AC-3 bitstream to the receiver) and compressed offload** | `DefaultAudioSink.configure` comment, line 794: "Audio processing is not supported in offload or passthrough mode." The bitstream can only be dropped or replaced by whole encoded frames (AC-3 32 ms, E-AC-3 up to 32 ms per frame, DTS about 10.7 ms), and an AudioTrack-level volume does not alter an encoded bitstream. So with passthrough, mute is not sample-accurate and may not work at all. | VERIFIED-FROM-SOURCE (processing unsupported); INFERRED (volume ineffective on bitstream passthrough) |
| What then for passthrough households | (a) Force PCM decode in the filtering player: disable passthrough capabilities (the app computes `isPassthroughAudioAvailable` from `AudioCapabilities`, `util/profile/codec/audioPassthrough.kt`, and feeds the device profile; a fork can make that return false and set the sink to PCM), so the app decodes AC-3/E-AC-3 on the box (platform decoder or the bundled `jellyfin-androidx-media3-ffmpeg-decoder`) and downmixes or outputs PCM. Cost: no Atmos/Dolby bitstream for filtered titles. (b) Drop whole encoded audio frames in a custom `ForwardingAudioSink` for a **cut** (still frame-granular). (c) Mute is simply unsupported on passthrough; mark such a plan "needs PCM". Per press reviews the 2026 box passes Dolby Digital Plus/Atmos-in-DD+ but not TrueHD/DTS-HD (DOCUMENTED, https://blog.lon.tv/2026/05/10/new-onn-4k-pro-streaming-box-review-2026-version/ and androidtv-guide; one conflicting GPU spec noted between sources). Stock app issue #5977 (open, 2026-11-19) shows passthrough direct-play behavior is currently shifting in 0.20.0 (DOCUMENTED, https://github.com/jellyfin/jellyfin-androidtv/issues/5977). | mixed, as tagged |

### 2.4 Seek, resume, tunneling, HLS

- **Seek into a removed range.** With clipped items, the removed range is simply absent from the timeline, so a seek cannot land in it (the Web lab result, replicated by construction). The seek bar/position mapping layer is custom work. (INFERRED)
- **Resume.** Jellyfin stores resume ticks in source time; the fork must convert both ways, and a stored position inside a cut must snap forward to the next kept time. (INFERRED)
- **Tunneled video playback.** Not enabled in the app that I found (`DefaultTrackSelector` only sets audio offload); tunneling would bypass app-side audio processing and A/V timing control. I did not grep for `setTunnelingEnabled`. (INFERRED; flag as an unknown)
- **HLS.** Clipping an HLS server transcode works in Media3 but segment-aligned (`HlsMediaPeriod.getAdjustedSeekPositionUs` snaps to a segment/keyframe like progressive), and every clip would need either one long transcode URL with the app skipping frames, or separate sessions. Practical rule: filtering plans apply to **direct play** (static progressive stream) only; force direct play for filtered titles and show "cannot protect this title on this box" otherwise. (INFERRED; the Web lab also marks HLS/transcode as unverified.)
- **Subtitles.** External subtitle configurations attach to the whole `MediaItem`; with clipped items, each clip's subtitle sidecar would need the same offset logic. Embedded text tracks follow the clip timeline automatically. Captions inside a marked range would be visible until clipped away by the timeline. (INFERRED)

## 3. Delivery options

### 3(a). Maintained fork of jellyfin-androidtv, sideloaded

| Aspect | Finding | Tag |
| --- | --- | --- |
| What to change | Smallest change: in `VideoManager.setMediaStreamInfo` build a `ConcatenatingMediaSource2` of `ClippingMediaSource`s from a reviewed Cleanyfin plan (fetched per item), set a position map used by `PlaybackController` for seek/progress/resume, add a PCM-only mute `AudioProcessor`, and block the fallback to unfiltered playback when a plan is required but missing. Roughly 500-1000 lines of new Kotlin/Java in 4-6 files, plus plan fetching. | INFERRED |
| Build chain | Gradle with Kotlin 2.4.21, compileSdk 37 (local SDK has only `platforms/android-36`; compileSdk 37 would need a platform download), Media3 1.11.1, bundled ffmpeg decoder `org.jellyfin.media3:media3-ffmpeg-decoder:1.9.0+1`. | VERIFIED-FROM-SOURCE (`libs.versions.toml`); local SDK listing VERIFIED by `ls` |
| Coexistence with the Play Store app | Debug build type has `applicationIdSuffix = ".debug"` "to run release and debug at the same time" (`app/build.gradle.kts` lines 73-83). A release fork should set its own `applicationId`/app name and its own signing key; the same ID with a different key cannot update or replace the Play install. | VERIFIED-FROM-SOURCE (debug suffix); INFERRED (release recommendation) |
| Upstream churn | Repo pushed hours before this pass (2026-11-21); Media3 bumped 1.10.0 -> 1.11.1 in six months (PRs #5491, #5815); the rewrite stack and `TimedEvent` API landed Feb-Mar 2026 (#5440, #5450). Rebase cost is real: expect a monthly merge for a year while the rewrite stack settles. | DOCUMENTED (PR list) |

### 3(b). Upstream contribution

| Item | State and date | Tag |
| --- | --- | --- |
| PR #4567 "Adds a profanity filter like Vidangel" (custom word blacklist, mutes audio on blacklisted words, masks subtitles) | **Closed unmerged 2025-05-10**; maintainer comment: "Closing as invalid and stale". | DOCUMENTED, https://github.com/jellyfin/jellyfin-androidtv/pull/4567 |
| PRs #5440 (2026-02-27) and #5450 (2026-03-02) timed events and blocks | Merged. Purpose stated as foundation for media segment actions, ask-to-skip, "preloading (gapless playback)". This is the extension point a contribution would build on. | DOCUMENTED, https://github.com/jellyfin/jellyfin-androidtv/pull/5440 and /5450 |
| Issue #4177 skip button for whole segment | Open, updated 2026-05-23 | DOCUMENTED |
| PR #5800 skip-button duration preference | Open, updated 2026-11-09 | DOCUMENTED |
| Android TV issue or PR for custom segment types, a mute action, or pre-buffered/exact skipping | **None found** in `jellyfin-androidtv` (searches: segment, mute, profanity, custom segment, gapless; results above). | DOCUMENTED (absence in search; not exhaustive) |
| Server side: jellyfin/jellyfin #17807 / PR #17808 "Plugin hooks for per-session FFmpeg audio filters and edit graphs" (open since 2026-09-06, updated 2026-11-16) | An alternative server-side route (a plugin edits the transcode graph); outside path B but it shows maintainers are being asked for edit hooks. Note Jellyfin removed custom FFmpeg flags in 10.8.13 for security (GHSA cited in the issue). | DOCUMENTED, https://github.com/jellyfin/jellyfin/issues/17807 |
| Swiftfin #2438 and #2447 (configurable / customizable MediaSegments) | Open (2026-11-15, 2026-11-17): shows client demand for configurable actions, none for Android TV. | DOCUMENTED |

Verdict inputs: the maintainer who closed #4567 is the same one who wrote the timed-event API, and the app has no concept of "hide content" actions beyond skip. An upstream patch for exact pre-presentation removal changes the player timeline model, which is a large review ask; a household-content filter has been closed once as invalid. Plan on **not** upstreaming the filter, but a small upstream-able change (e.g. allow extra segment types to map to configurable actions in preferences) is plausible. (INFERRED)

### 3(c). Separate small Cleanyfin player app (Jellyfin Kotlin SDK + Media3)

- SDK: `jellyfin/jellyfin-sdk-kotlin`, license **LGPL-3.0**, active (pushed 2026-11-20). (VERIFIED via GitHub API)
- Scope you own: login/server select, library browse and item pick UI for a TV remote (leanback or Compose for TV), playback screen, direct-play URL build with `videosApi.getVideoStreamUrl(static = true)` (same call the app's external-player path uses, `ExternalPlayerActivity.kt:112`), segment/plan fetch, progress and played reporting (`PlaySessionService`-equivalent), audio/subtitle track pickers, resume, next-up. All the TV UX that the household gets from the stock app would be reimplemented. (INFERRED)
- Advantage: designed around the edited timeline from day one; no fork rebase; any license you choose for your own code (LGPL SDK and Apache-2.0 Media3 are linkable from AGPL or proprietary code under normal LGPL rules; **INFERRED, not legal advice**).
- Minimal variant: **no browse UI**. Launch it only from an external-player intent or a household-specific "play this item id" intent (see 3(d)), play one item, report progress, exit. This is the smallest honest version of (c).

### 3(d). External-player intent in v0.20.0

It exists. `PlaybackLauncher` routes to `ExternalPlayerActivity` when `useExternalPlayer` is on and all items are movie/episode/video/etc. (`PlaybackLauncher.kt`). The activity builds an `ACTION_VIEW` intent with `setDataAndTypeAndNormalize(videoUrl, "video/*")` (line 128), where `videoUrl = api.videosApi.getVideoStreamUrl(itemId, mediaSourceId, static = true)` (line 112), optionally pinning the player component chosen in settings. It then asks the player's `ExternalPlayerApi` to add extras. `ExternalPlayData` carries exactly: `url`, `title`, `fileName`, `externalSubtitles` (stream, url, name, language), `position`. The default API only adds `title` and `position`; the MPV API adds `title`, `position`, `subs`, `subs.enable`, and reads back `position` (`DefaultExternalPlayerApi.kt`, `MpvExternalPlayerApi.kt`). Other implementations present: MX, Vimu, VLC. (VERIFIED-FROM-SOURCE)

What this means: the external player receives a **direct-play URL, item title, file name, subtitle URLs and resume position, but not the Jellyfin item id, the media source id as a field, user, or segments**. A Cleanyfin player would have to recover the item from the URL path (`/Videos/{itemId}/stream?...&mediaSourceId=...`), which also normally embeds an access token or api_key (INFERRED: the SDK URL builder appends credentials; not traced) and use it to fetch the plan. Nothing in the intent contract identifies the signed-in Jellyfin user other than that token. External players also receive the position in milliseconds and return a result; the app marks the item watched on `Success` (`onPlayResultSuccess`), so a Cleanyfin player must return the same result convention to keep resume behavior sane. Also the external path always direct-plays, which is what filtering needs. (VERIFIED for data passed; INFERRED for consequences.)

A custom player package must be a pinned component name in settings (the app lets the household pick the external app, `externalAppRepository.getCurrentExternalPlayerApp`), which is a per-box configuration step.

### 3(e) Weak options considered and rejected for exactness

- Stock app plus Commercial-type segments: rejected by the lab (marked frames shown before the jump).
- Pre-rendered edited files on the server: outside path B (measurement reference in the earlier lab).

## 4. Google TV practicalities

| Topic | Finding | Tag |
| --- | --- | --- |
| Hardware | onn. 4K Pro (2026): Amlogic S905X5M quad A55, 3 GB RAM, 32 GB storage, Android TV 14 (32-bit userspace per one review), AV1/VP9/H.265, Dolby Vision, Widevine L1, Dolby Digital Plus/Atmos passthrough, no TrueHD/DTS-HD MA passthrough, USB 2.0, Ethernet 100 Mbps. | DOCUMENTED (https://www.androidtv-guide.com/streaming-gaming/onn-walmart-4k-pro-js620k4/, https://blog.lon.tv/2026/05/10/new-onn-4k-pro-streaming-box-review-2026-version/); not confirmed on the household unit |
| Sideload | Developer options (tap Android TV OS build 7 times), USB/network debugging, then `adb connect <box>` and `adb install`; or Downloader with "install unknown apps". | DOCUMENTED (search results: anexplorer.io, iptvcommons.com, firestickdownloader.com) |
| Google developer verification for sideloaded apps | Reported: unverified developers need an advanced flow with a waiting period, **ADB install is a stated exemption**. Rule details and rollout on Google TV for a 2026 box were not verified against Google's own page. | DOCUMENTED via third-party (https://onntvhacks.com/new-android-sideloading-rule-for-onn-tv/); UNVERIFIED primary |
| Updates | A sideloaded fork or custom app does **not** auto-update. Each Cleanyfin release means an `adb install -r` over the network, or a household-run Downloader flow. A different applicationId avoids Play Store overwriting it. For a one-household product that is a recurring chore for the owner. | INFERRED |
| Play Store coexistence | Yes with a distinct applicationId (debug already does). Both appear in the launcher; the TV banner must be set. | VERIFIED-FROM-SOURCE (suffix) / INFERRED |
| License: app is GPL-2.0 | GitHub reports GPL-2.0 and `LICENSE` is the GPL v2 text (VERIFIED). A fork is a derivative work: all distributed fork changes must be offered under GPL-2.0 with source. For a fork installed only by the owner on the owner's box there is no distribution, but any sharing with other households is distribution. | VERIFIED (license) / INFERRED (consequence) |
| Interaction with Cleanyfin AGPL code | I did not retrieve GNU's FAQ (429). From general knowledge, **GPL-2.0-only and AGPL-3.0 cannot be combined into one program** (GPLv3-family licenses are only compatible with GPLv2 if the GPLv2 work is "or later"; I did not check the fork's per-file headers for "or later" language). Separate programs that talk over HTTP (Cleanyfin server/plugin AGPL, Android client GPL-2.0) are fine. So: keep the plan-resolution logic on the server or in a separate library under a license compatible with GPL-2.0, and copy no AGPL code into the fork. Needs a lawyer or at least a read of per-file headers before any distribution. | INFERRED, flagged for owner |
| Option (c) separate app | Free to choose its own license; may link LGPL SDK and Apache Media3. Do not copy GPL code (e.g. from the Jellyfin app) into it. | INFERRED |

## 5. Testing without the physical box

| Item | Finding | Tag |
| --- | --- | --- |
| Local SDK | `~/Android/Sdk/system-images/android-35/google_apis/x86_64` is installed (a **phone** image; `sdkmanager --list` shows it under Installed). `system-images/android-35/default` and `android-36/google_apis_playstore` directories exist but are **empty** (not installed). Emulator 37.2.12.0, build-tools 36.0.0, platform android-36, `/dev/kvm` present. One AVD, `obsidian_test` (unrelated). | VERIFIED (local `ls`, `sdkmanager --list`) |
| TV images available to download | No TV images exist for API 35. TV images that do exist: `system-images;android-34;android-tv;x86` and `;google-tv;x86` (plus arm64), and `system-images;android-36;android-tv;x86_64`, `;google-tv;x86_64`, `;google-tv-ps16k;x86_64` (also x86 variants and arm64 for 36). Closest match to the box (Android 14 = API 34): `system-images;android-34;google-tv;x86` or android-tv; API 36 Google TV x86_64 is the best fit for the current emulator and the app's `targetSdk 36`. Needs `sdkmanager --install` (network, a few GB) and `avdmanager create avd` with a TV device definition (e.g. `tv_1080p`). Not done in this pass. | VERIFIED (sdkmanager listing); install steps INFERRED |
| What the emulator cannot show | Real vendor decoders (the box uses Amlogic hardware; the emulator uses software/ goldfish codecs), real HDMI/audio passthrough, real frame-release timing, real startup/rebuffer cost. Emulator results prove **logic and exposure of the timeline**, not hardware behavior or passthrough. Final acceptance still needs the real box. | INFERRED |
| Measuring rendered frames | (1) Media3 on any device: attach an `AnalyticsListener`/`VideoFrameMetadataListener` (`onVideoFrameAboutToBeRendered` gives each frame's presentation time and release time) and log pts for every frame handed to the surface; plus `onRenderedFirstFrame`, `onDroppedVideoFrames`. That gives a per-frame list to compare against the marked interval, independent of any seek acknowledgement. (2) A fixture where marked content is a solid color (as in the Web lab) allows an independent check from `adb exec-out screenrecord` or `adb shell screenrecord --output-format=raw-frames`/`screencap` loops, then count red frames offline with ffmpeg. `screenrecord` is usually compressed and capped (3 min default), so use it as a corroboration, not as the measurement. | DOCUMENTED (Media3 APIs exist; `VideoFrameMetadataListener` is part of Media3 ExoPlayer API; not checked in source this pass), INFERRED (procedure) |
| Measuring audio | Insert a recording `AudioProcessor`/`TeeAudioProcessor` (Media3 `TeeAudioProcessor` writes PCM passing through the sink) to capture exact PCM with timestamps before AudioTrack, then analyze for the 880 Hz marker in the removed range. For the output side, `adb shell` cannot reliably record the system mix on a TV image; treat processor-tap evidence as "pre-sink". | INFERRED (name `TeeAudioProcessor` from memory of Media3; not verified at 1.11.1 this pass) |
| Test automation | Instrumentation test on the fork (`androidTest`) with a Media3 `ExoPlayer` + test listener, run with `./gradlew connectedDebugAndroidTest`; or a bare Media3 test app (see prototype). Media3 test utils (`media3-test-utils`, `FakeClock`, `TestPlayerRunHelper`) help with deterministic playback loops but run without real codecs under Robolectric, so frame output needs the emulator or the box. | INFERRED |
| Fixtures | Reuse the lab fixtures (`438f960a...` three-section file) and generate H.264/AAC and E-AC-3 variants with cut points **inside** a GOP and inside an audio frame, which the 80-frame fixture did not cover (the `10-...` doc's own "next engineering gate"). | from the existing docs |

## 6. Verdicts

| Option | Viability | Why |
| --- | --- | --- |
| (a) Fork of jellyfin-androidtv sideloaded | **Viable, highest fidelity, highest recurring cost.** Keeps the stock UX the household already knows; exactness depends on 2.1-2.3. | Reuses all TV UI. Costs: rebase against an active repo, own signing/updates by `adb`, GPL-2.0 obligations, and AGPL separation. |
| (b) Upstream contribution | **Not viable for the filter itself** (large model change, precedent #4567 closed as invalid/stale, no segment-type/mute/gapless issue exists). A tiny enabling PR is possible. | |
| (c) Separate Cleanyfin player (full app) | **Viable but the largest build.** Cleanest timeline design; all TV UX is yours. Not worth it unless (d) fails. | |
| (c') Minimal Cleanyfin player launched via the stock app's external-player intent (option (d)) | **Most promising for a first household test**: small codebase (single Activity, Media3, plan fetch), no fork, own license, own update cadence. Weaknesses: external-player intent carries only URL/title/subs/position, so item id and token are recovered from the URL; resume/watched reporting depend on returning the expected result; household must switch the stock app to external player and pin the component. Stock app's own non-filtered path remains one setting away. | |
| (d) External-player intent alone with a third-party player (MPV, VLC, MX) | **Not viable** for exact filtering without custom code: those players do not know Cleanyfin plans (mpv could use an EDL via script, but the Android intent path passes no plan). | INFERRED |

Exposure caveats that apply to **every** native option: audio exactness fails the "any marked sound" rule at the cut edges unless audio is PCM and edited by a custom processor, or cut points align to audio frames (2.1, 2.3); passthrough Dolby loses sample-level control; no measurement exists yet of the join's black/hold behavior.

## 7. Smallest lab prototype that proves or kills the path

**Build** (about 1-2 days): a tiny Android app (one Activity, Media3 1.11.1 `ExoPlayer`, no Jellyfin SDK, no UI beyond a `PlayerView`) that plays a local progressive file from app storage or a loopback HTTP server, taking a hard-coded plan `[(0, 3.2s), (6.4s, end)]` as `ConcatenatingMediaSource2` of two `ClippingMediaSource`s (`startsAtKeyFrame=false`), with a recording `AudioProcessor` and a `VideoFrameMetadataListener` that logs every presented frame's pts and every PCM block's pts. Add the mute `AudioProcessor` as a second mode (zero samples in `[start,end)` in source time) over one uncut file.

**Fixtures** (new): synthetic H.264/AAC-LC and H.264/E-AC-3 where the cut start and end fall **inside** a GOP and **inside** an audio frame, with a distinct color and tone just before and just after the cut (so lost-or-leaked material is countable), plus one 4 s red/880 Hz marked block.

**Run** on the Android 14 / 36 TV emulator (download an image first) and then, with owner permission, on the box. **Measure**: (1) count of rendered frames inside the marked interval (pass = 0); (2) count of allowed frames missing next to the cuts (preserved material); (3) maximum PCM samples with energy inside the interval, per codec; (4) join gap: max inter-frame pts gap and wall-clock hitch at the splice; (5) seek into the cut, seek across the cut, and cold resume at a stored source position; (6) repeat with passthrough forced on vs off on the box; (7) memory/startup with 50 clips (a long film with many marks) to size the per-clip cost.

**Kill criteria**: any rendered marked frame; audible/measurable marked audio after switching to PCM mode; a visible join stall longer than a few frames; or per-clip cost that makes a 50+ cut film unplayable.

## 8. Estimated build effort

| Piece | Estimate |
| --- | --- |
| Lab prototype above | 1-2 days, plus 0.5 day for emulator image/AVD setup |
| Minimal external-player Cleanyfin player (c') on top of a proven prototype: item-id/token recovery, plan fetch, position map, progress/result reporting, "no plan -> refuse" screen, signing and install script | 1-2 weeks |
| Fork (a): plan fetch, timeline builder, position map through `PlaybackController`, seek/resume/progress, PCM mute, refusal screen, plus build environment (compileSdk 37 platform, Gradle, signing) | 2-4 weeks, then ongoing monthly rebase |
| Full separate app (c) | 6-10 weeks for a household-quality TV UI |

(All INFERRED estimates.)

## 9. Open unknowns

1. Real frame-level leakage and splice behavior of Media3 clipping on the Amlogic box (software emulator will not reveal it).
2. Audio edge exactness by codec: whether AAC/E-AC-3 decoder output buffers on this hardware align to frames, and whether the PCM mute processor can map presentation time reliably across seeks and flushes.
3. Whether the box's platform decoder or the bundled ffmpeg decoder is used for E-AC-3 when passthrough is turned off, and the audio-quality/latency consequence for the household.
4. Whether `startsAtKeyFrame=false` joins cause a codec reset and a visible stall on this hardware.
5. What credentials the external-player URL carries (api_key vs header) and whether recovering the item id from it is stable across 0.20.x.
6. Current Google developer-verification rules for sideloaded apps on Google TV (third-party reporting only), and whether `adb` install remains exempt.
7. Per-file license headers in jellyfin-androidtv ("GPL-2.0-only" vs "or later") and the exact AGPL separation needed.
8. Tunneled playback and audio offload settings on the box (the app enables offload in the track selector); whether offload bypasses processors in practice.
9. Whether `skipTypes` in the rewrite stack can ever include `Unknown`; irrelevant for filtering if the fork builds its own timeline, but relevant to whether a one-line upstream change could help.
10. Behavior on server-side transcodes (HLS) and multi-edition items; the plan says: force direct play and refuse otherwise.
