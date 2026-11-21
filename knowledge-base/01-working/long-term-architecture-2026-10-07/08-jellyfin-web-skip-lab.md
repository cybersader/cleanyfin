# Jellyfin Web 10.11.11: a native auto-skip is not a no-exposure filter

Lab, 2026-11-18/19. Partial E1/E4 experiment, **not** a release certification. The owner's predeclared fail condition: any detected marked picture or sound fails. Source revision for the active Cleanyfin DLL was not independently established; its SHA-256 is below. Synthetic rows were seeded in an isolated database and their API result was checked; this is a test of player behavior, not of plugin ingestion. No household media or shared Jellyfin instance used.

## Native control

- Official `jellyfin/jellyfin:10.11.11` image digest `sha256:aefb67e6a7ff1debdd154a78a7bbb780fd0c873d8639210a7f6a2016ad2b35db`, bundled Web in desktop Chromium, HTML video, fresh viewer. First test: 15-second 320×180 H.264/AAC clip at 25 fps / 48 kHz. Blue/silence except red/880 Hz during [4,8). Four seconds = 100 source frames. DLL `228a18dca3d3d8f4564dacee0d1998abfef3698e9c3e4cb2da93d4660935cbc3`.
- Default viewer: segment API returned `Unknown` [40000000,80000000) ticks when explicitly queried; Web requested Intro and Outro only. Browser video-frame callbacks and a 1-pixel canvas detected all 100 red frames starting from zero, after seeking to 2.5 s, and after pausing/seeking to 3 s and continuing. This was same-session pause/resume, not a cold restart.
- Actual playback settings UI listed Intro, Preview, Recap, Commercial, Outro, **not Unknown**. [Pinned UI source](https://github.com/jellyfin/jellyfin-web/blob/v10.11.11/src/components/playbackSettings/playbackSettings.js) explicitly excludes Unknown. [Player source](https://github.com/jellyfin/jellyfin-web/blob/v10.11.11/src/apps/stable/features/playback/utils/mediaSegmentManager.ts) seeks after entering an interval and intentionally suppresses some seek-back and short-segment skips.
- Positive control: change only the test row type to Commercial and use the player's normal UI to save Commercial = Skip. Web requested Commercial; it really sought to 8 s. But **4 red frames** at 4.00, 4.04, 4.08, 4.12 s were observed before the jump. Seek from after the interval into 6 s: **49 red frames** observed (6.04–7.96 s), no automatic seek observed in that run. Separate seek into the marker: browser captured-audio analyser peak 0.30035. Frame counts are observed, not a maximum leakage bound. Relabeling household marks as ads also collides with legitimate ad preferences; not a recommended fix. Restored row to Unknown. Lab server stopped.

## Pre-play preparation control (offline, local to lab)

Used bundled FFmpeg to decode, `trim`/`atrim`, reset timestamps, concatenate the allowed ranges, then encode H.264/AAC MP4. Served the resulting file as a separate test item in the same Jellyfin Web player. No change to Jellyfin or shipped Cleanyfin software. No production architecture chosen.

| Clip | Decoded red frames | Decoded audio on the otherwise silent fixture |
| --- | ---: | --- |
| Original | 100 of 375 | Peak 0.30379 |
| Remove exactly [4,8) | 0 of 275 | 384 nonzero samples; peak 0.002178. This is a measurable residual, not evidence it was audible or intelligible. |
| Remove [3.92,8.08) | 0 of 271 | All samples zero. Costs two extra allowed video frames on each side. |

Padded fixture in Jellyfin Web: 226 blue frames and no red sampled up to 9 s on initial play. From zero, seek to 3.5 s and resume at the splice (3.92 s): no sampled red and browser audio analyser peak zero across the splice in each run. Positive tone control above verified the browser audio tap. Decoded samples and browser audio are not physical loudspeaker/display measurements.

Second fixture guarded against a false success from simply blanking or silencing everything: blue/440 Hz → red/880 Hz → green/660 Hz. Padded output had 98 blue frames, 173 green, zero red. Dominant frequency in every complete 100 ms FFT window was either 440 or 660 Hz (not a proof of zero weak spectral leakage). In Web, 226 observed frames to 9 s: 98 blue, zero red, 128 other; offline decoding identifies the latter color as green. Not tested: arbitrary boundaries/codecs, TV clients, alternate audio/subtitles, different editions, server transcodes, end-to-end profile selection, startup cost or cold resume. **80 ms is a test input, not a safe universal margin.**

Fixture SHA-256s: original `ad9ecfe3da9aa8d804edf3a48390314cf0b18bf5d247dad5c56b47ab8c81dbfe`; exact `54f713e070c61e6301bfec675bac4f80a1bb090741f2036c552f78968464b25f`; padded `1be680b38dc91743cf7c7b41ebb4845be417ff628f292385d677f1093840c735`; second original `5a5e6d45a2656da882008ea1dab37211b249219dea61e04dd614ea04575eb56a`; second padded `3af38137a326c94946478b8353245d8e5affc248643c7881177329be6ba15e08`.
