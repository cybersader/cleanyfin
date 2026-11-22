# Four ways to deliver filtering: comparison, 2026-11-21

Synthesis of four source-reading research reports (A to D in this directory). Nothing below was run; every verdict is a hypothesis until its prototype is measured. Owner's pass rule: any detected marked picture or sound fails, and material outside the mark is preserved.

| Path | Works with the stock Jellyfin app on the onn. box? | Exact picture cut | Exact sound cut or mute | Biggest unknown | Smallest decisive test |
| --- | --- | --- | --- | --- | --- |
| A. Server serves an already-cut stream | Possibly, as a separate "clean version" in its own library; the original stays the default source otherwise | Yes if boundary GOPs are re-encoded | Yes if audio is re-encoded at boundaries | Whether the Android TV app accepts a plugin-served HLS source; re-encode cost on a NAS | Plugin HLS controller plus one strm item, measured in Web and an Android TV emulator (2 to 4 weeks for a full single-audio prototype) |
| B. Filtering player on the box (Media3) | No; a small player launched through the stock app's external-player option, or a fork | Yes per Media3 source (decode from prior keyframe, drop outside the clip) | Not by default (about 21 ms AAC buffer may straddle a cut); needs PCM decode and a custom audio processor; impossible under passthrough | Real behaviour on the Amlogic hardware; how the hand-off player identifies the item | Bare Media3 app that logs every rendered frame and PCM block (1 to 2 days) |
| C. Code injected into Jellyfin Web | No; the Android TV app is native and Google TV has no web wrapper | No in the stock player (timeupdate at 4 Hz; MSE drops non-keyframes after a window start) | Maybe, browser only, via a scheduled Web Audio gain | Media-time to audio-clock mapping error | Injected gain-schedule mute with an in-graph leak meter (1 to 2 days) |
| D. Kodi or mpv with edit lists | No; replaces the stock app | mpv: yes per source. Kodi: packet-drop then seek, B-frame exposure unknown | mpv: no native interval mute. Kodi: whole audio packets (21 to 32 ms) | Kodi reads `.edl` only for local or share paths, not Jellyfin HTTP streams; mpv-android scripting on TV | Desktop generator for both formats, measured on a mid-GOP B-frame fixture (3 to 4 days) |

## What this means

- For the household's TV, only A and B are candidates. C cannot reach the box. D means Kodi or mpv instead of the Jellyfin app.
- A and B fail in different places: A's risk is whether stock clients accept the stream and what it costs the server; B's risk is audio exactness and the hand-off.
- Every path needs the same two things first: a hard test fixture (cut inside a GOP with B-frames and inside an audio packet, with identifiable neighbours on both sides) and one cut-plan format that each prototype consumes. Building those once keeps the four comparisons honest.

## Owner decisions raised by the research (not yet asked)

- Whether covering the picture on screen (rather than removing it) counts as a pass in a browser.
- Whether a calibrated mute error of a few milliseconds is acceptable.
- Whether the family would accept Kodi or mpv in place of the stock Jellyfin app.

## Unverified points to check before relying on them

GPL-2.0 (jellyfin-androidtv) and AGPL in one binary; Google TV sideloading rules; line-level references for path A.

## First measurements, 2026-11-21 (shared hard fixture; cut inside a GOP and inside an AAC packet)

Run on a Linux desktop in containers. No Jellyfin server, no Android device and no real client was involved. The audio scorer is thresholded (5 ms windows), not sample-exact.

| Prototype | Marked picture shown | Marked sound (880 Hz) | Kept material lost | Notes |
| --- | --- | --- | --- | --- |
| mpv 0.40.0 playing the kept-range EDL | 0 of 126 frames; all 374 kept frames present, in order | 0 ms | About 10 ms of sound right after the splice differs from the reference (448 PCM frames; [superseded on 2026-11-22: measured with video disabled](../exact-audio-2026-11-22/01-audio-at-the-join.md)) | Seek into the removed interval and seek back across the splice showed no marked frame. Missing or malformed EDL: exit 2, no fallback to the original. |
| mpv mute (no cut, sample-index volume filter) | n/a | 0 ms; zero non-zero PCM frames inside the mark | none after the mark | Desktop only. |
| Kodi 21.2 with a cut `.edl` | Leaked: source frame 255 appeared in 12 screen snapshots | 0 ms detected | Inconclusive (screen capture ran at about 17 fps and the control missed frames) | Fails the owner's rule on this fixture. |
| Server-style HLS packager (pinned Jellyfin ffmpeg, MPEG-TS) | 0 marked; passes four decoder variants including two seeks | 0 ms | none beyond scorer tolerance | 300 video frames stream-copied, 74 re-encoded; about 10 s to package the 20 s clip; audio re-encoded once. fMP4 was rejected because an ffmpeg seek landed 1 s late. Offline ffmpeg decoding only. |

`run-mpv.py` and `check-hls.py` were each run twice independently; the numbers above match both runs. Kodi was not rerun. A constant 10 ms loss of the 440 Hz tone at the very start of every mpv capture, control included, is a capture artifact and is not counted ([superseded on 2026-11-22: measured with video disabled](../exact-audio-2026-11-22/01-audio-at-the-join.md)).

## Media3 test player on a Google TV emulator, 2026-11-21

Emulator: a Google TV AVD (Android 16 / API 36, arm64, headless, `-no-audio`) on a separate arm64 macOS host, driven with adb. Media3 1.11.1, two `ClippingConfiguration` items, software decoders `c2.goldfish.h264.decoder` and `c2.android.aac.decoder`, no tunneling or passthrough. APK SHA-256 `ce913428930d5c4b9652cf92372b0a47ab50f139616a2dd0ad9937a76acf3669` (one repair after the first build: `hideBars()` moved after `setContentView()`).

Picture numbers are the app's own log of frames handed to the renderer, mapped to source frames by the app; PCM is what reached AudioTrack, not speaker output. A screen recording corroborated the control and the missing frame 263 in `cut_pcm`; the `cut` recording stopped at frame 47. Marked and missing frames were recounted from the raw `frames.csv` files with the same result.

| Run | Marked frames rendered | Kept frames lost near the splice | 880 Hz leak | Kept sound lost or altered at the splice | Largest gap at the splice |
| --- | --- | --- | --- | --- | --- |
| original (control) | 126 of 126 (valid control) | none | 5040 ms | n/a | n/a |
| cut | 0 | 263 and 264 | 0 ms | 896 samples of 440 Hz missing before, 832 samples of 660 Hz altered after | 217 ms |
| cut_pcm | 0 | 263 | 0 ms | 1024 missing before, 832 altered after | 200 ms |
| cut, seek into the mark | 0; landed on 263 | none | 0 ms | 832 altered after | n/a |
| cut, seek back across the splice | 0 | 263 on both passes | 0 ms | 896 before, 832 after, each pass | 217 ms |
| cut_pcm, seek into the mark | 0; landed on 263 | none | 0 ms | 832 altered after | n/a |

Reading: no marked picture or sound in any filtered run. Playing straight through the join drops one or two kept frames (40 to 80 ms) and about 19 to 21 ms of sound before plus 17 ms after, with a stall of about 0.2 s. Starting playback at the splice shows frame 263, so the loss comes from the transition between the two clips, not from clip-start accuracy. Whether hardware decoding on the onn. box behaves the same is unknown. Nothing here is a result for the physical box.

## Stock Jellyfin Android TV 0.20.0 playing a server-cut item, 2026-11-21

Same emulator. Unmodified release APK (SHA-256 `dfcc665195bed4ea087dca63df6f77497acc1185c4b15776b336d80eeaeb7fa6`), signed in as a passwordless non-admin lab user on a disposable Jellyfin 10.11.11 container, loopback only. The filtered item was a `.strm` in a test library pointing at an HTTP URL served from the lab PC. Picture evidence is `adb screenrecord` scored by frame barcode plus independent `screencap` colour samples. Sound was not measured (emulator runs `-no-audio`; the stock app has no PCM log).

| Item given to the stock app | Screen recording | Screencap samples | Outcome |
| --- | --- | --- | --- |
| Original (control) | 132 marked presentations at about 24.9 fps | 17 red of 84 at 3.18 Hz | Valid control |
| Filtered HLS (MPEG-TS, boundary-only re-encode) | only frame 0 readable | 15 blue, all frame 0; 0 red of 69 | Did not play: loops at 0 with `failed to parse group ID [main:muxed:1]`, `trying to switch audio stream from -1 to 1`, `stop called at 0` |
| Same stream remuxed to one progressive MP4, no re-encode | all 374 kept barcodes, 0 marked, at about 24.4 fps | 0 red of 67; blue and green seen | Played to 14.954 s |

Observed facts:

- With the strm URL unreachable from the server, the item still appeared, but the app requested Jellyfin's `/videos/.../master.m3u8`; server ffmpeg logged `Network is unreachable` and Media3 got HTTP 500. So the server must be able to read whatever the strm points at.
- Once the server could probe it, the app fetched the playlist, segments, or MP4 directly from the URL; no server transcode started.
- PlaybackInfo reported direct play, direct stream and transcoding all true with a null TranscodingUrl for every source, including the HLS item that then failed. Those flags did not predict playback.
- The stock release exposes no toggle for the newer player backend.

Not reached: seeking in the stock app, sound, a second cut, the physical onn. box. A screen recording at 24 fps of a 25 fps clip is not proof of zero leak.

Design consequence: the packaging is sound and the stock app will play its output, but as delivered the HLS form is not accepted by the app's legacy player. Either the playlist is changed until that player accepts it (the error concerns the audio group of a muxed stream), or the server delivers a single filtered MP4 (pre-built, cached, or streamed as it is built).
