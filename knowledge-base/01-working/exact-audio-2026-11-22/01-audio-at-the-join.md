# Sound at a cut: can kept audio be exact?

**2026-11-22 · lab research, desktop mpv only.** Question from the owner: if kept sound at a cut can be made exact, find out how. "Exact" here means identical to decoding the uninterrupted original and then deleting exactly the marked samples.

## Answer

**Yes, with a catch.** Unmodified desktop mpv, given Cleanyfin's existing cut list and playing picture and sound together as a viewer would, reproduced every kept audio sample of the AAC test movie and skipped the marked material. The earlier "altered sound after the cut" result came from measuring with video disabled. The catch: a second test movie with lossless audio exposed a one-sample leak caused by rounding inside mpv, so this cannot be claimed in general, and nothing here has been shown on Android or a television.

## What was measured

All rows: real mpv in the lab container, decoded PCM compared sample by sample against uninterrupted decoding with the marked indices deleted, no alignment adjustment. Reproduce with `tools/playback-lab/prototypes/exact-audio/join_lab.py`.

| Test | Result |
| --- | --- |
| Existing cut list, H.264 + AAC, normal picture-and-sound playback | All 718,080 kept stereo samples identical. All 374 kept pictures in order, no marked picture, no marked tone. Same PCM over loopback HTTP. MEASURED |
| Same source and cut list, video disabled | 448 consecutive kept stereo samples differ just after the join; prefix and length exact. MEASURED |
| H.264 + AC-3 / E-AC-3, stock settings | Differs by 1 LSB over about 22,000 samples after the join; a static `cons_noisegen=1` decoder setting removed the difference relative to an uncut control with the same setting. MEASURED |
| Existing cut list, H.264 + lossless FLAC, omission `[0.240, 0.440)` s | One output sample (index 11,519) carried source sample 21,119, the **last marked sample**, in place of the last kept sample before the cut, and the output ended one sample short. A tone detector alone did not catch it; uniquely identifiable samples did. MEASURED |
| Experimental cut list for that FLAC case, 57 kept ranges | 602,880 of 602,880 stereo samples identical and all 314 kept pictures in order, none unwanted. MEASURED, not yet in the automated matrix |
| Seeks over HTTP on the AAC movie | Landed on the expected pictures (0, then 301, then 50). Three fresh-start positions emitted one extra preceding audio sample. MEASURED |

## Why

- **AAC after the join.** AAC frames overlap, so the first frame after a jump needs the frames before it to decode correctly. With video selected, mpv seeks to the preceding video keyframe and so reads 28 extra AAC packets before the boundary (packet 466 at 9.920 s instead of 494 at 10.517 s), which gives the decoder its history; it then trims to the sample. With video disabled it starts at the boundary packet and the first samples are wrong. VERIFIED by packet trace; MEASURED.
- **The one-sample leak.** `mp_aframe_clip_timestamps` converts a floating-point time difference to an integer sample count. At some boundary positions the rounding lands one sample on the wrong side. No codec overlap is involved; it shows with lossless audio because nothing else masks it. VERIFIED-FROM-SOURCE (mpv, lab version); MEASURED.

## Options

| Option | Effect | Cost |
| --- | --- | --- |
| Keep the existing joint picture-and-sound cut list for H.264 + AAC | Exact on the tested movie | None. Must be measured the way a viewer plays it, with both tracks. |
| Separate picture and sound partitions in one EDL (`!new_stream`, `!delay_open`), each pointing at the same original URL, with each non-zero boundary written a quarter of an audio sample earlier for video and later for audio (5.208 microseconds at 48 kHz) | Removed the one-sample leak on the FLAC movie; the sets of kept samples and pictures do not change, so it is not a trim margin | The renderer needs more than three decimal places. Must NOT be applied to AAC by analogy: separating the tracks can remove the decoder history that makes AAC exact. Needs its own evidence row. |
| Static `cons_noisegen=1` for AC-3 / E-AC-3 | Removes the 1 LSB difference | A one-time player setting; changes the decoder's noise realisation versus factory settings. |
| Fix the rounding upstream in mpv | Removes the leak at its source | A contribution to someone else's project; a person has to write it up. |
| Decode continuously and delete samples in PCM (filter graph) | Not pursued | Has to decode through the whole cut, and cannot be expressed from a cut list alone. INFERRED |

## The boundary

- Shown on desktop mpv with decoded PCM. Android TV is **not** shown: the harness's audio capture sits after Android's audio system and the emulator, and its earlier "20 ms altered before the join" came with a roughly 19 ms latency offset on the uncut control, so it cannot say which decoder samples changed.
- Audio passed through undecoded to a receiver (Dolby, DTS) cannot be cut to the sample by any player; exactness there can only mean whole coded frames.
- Exactness depends on codec, container, cut position and start or seek position. Each combination needs measuring; a pass on one movie does not transfer.

## Consequences for the project

1. The lab's mpv checks (`run-mpv.py`, `check-plugin.py`) measured sound with video disabled and so reported a flaw the viewer path does not have. They should capture picture and decoded sound from the same playback.
2. Tone detection is not enough to prove absence of marked sound. The fixture needs uniquely identifiable samples, and the acceptance gate should require exact samples and equal length.
3. The FLAC candidate goes into the automated matrix beside the failing stock control before any renderer change.
4. The evidence register's desktop-mpv row predates this and records kept sound as failed; it should be re-measured under the joint method, not edited by hand.
