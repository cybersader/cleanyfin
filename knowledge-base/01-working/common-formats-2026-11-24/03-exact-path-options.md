# Exact playback: keep the edit in the player

**Design proposal · 2026-11-25. Keep the plugin metadata-only. Test an upstream-rounded mpv on unchanged, sample-clocked originals first.** A server that decodes sound solves a different problem from a player that clips it at the wrong sample. Even perfect input PCM does not make the stock player exact. No option below admits a household layout today; keep refusing unmeasured combinations, including the original Wholphin hard fixture.

```text
household's original ── Jellyfin's authorized, unchanged bytes ──┐
                                                               ▼
reviewed marks + household policy ── plugin: timed keep list ── Wholphin / libmpv
                                                               │
                         correct source clock + decoder history + exact output gate
                                                               ▼
                                            kept pictures and PCM, in order
```

## Which path is worth pursuing?

| Path | Reaches R21? | Cost, risk and dependency |
|---|---|---|
| Original tracks + upstream-rounded libmpv | Candidate for narrow layouts; not a general decoder fix. | No new service or client. Start with sample-clocked MP4 E-AC-3 with deterministic decode, or MP4 AAC-LC independently verified free of PNS. Do not infer PNS from codec, bitrate or a quiet test scene. Depends on upstream adoption **and** measured Wholphin delivery. |
| Split EDL: original picture, **uncut** decoded PCM sound on its source clock | **No on stock in the indexed test below.** With corrected rounding, test the whole path, not just a WAV splice. | Eliminates compressed-audio seek state on the client. Adds a household media processor, bandwidth, seek latency, a new clock and a legal/scope decision. FLAC reduces bytes but adds framing, seeking and sample-format constraints. |
| One **already cut** PCM stream on the filtered clock; only picture uses multiple EDL segments | No on stock as tested. Removing marked samples before transport prevents a sound leak from those bytes, not the loss or duplication of kept sound. | Easier to isolate faults; creates reproducible edited sound on demand. Weaker fit to policy than uncut PCM. Not a fallback. |
| Move the bookmark away from the join | No. A bookmark is a request, not control of every decoder start or seek. | Cheap but changes what the viewer sees. Starting later loses kept content; starting earlier replays it. No proven universal distance, and no fix for the straight cut. |
| Silence instead of cutting | No for a **cut**. Candidate only for an explicitly requested **mute** with its own oracle. | Continuous local decode can preserve history across a mute. Requires a player-supported sample gate, precise seeking and decoded output. Not a timed remote volume command. |

## Counter test: can a flawless PCM source rescue the split EDL?

Use a synthetic 20 s H.264 fixture (500 distinct 25 fps pictures, 50-frame GOP with B-frames). Its 48 kHz mono signed-32-bit sound sample *n* has value *n*: even one wrong sample identifies its source, not just its loudness. The uncut MOV must return all 500 distinct frame hashes and integers 0…959,999 in order. The EDL uses the same picture as a video-only MP4 and an independent WAV: ideal bytes from an imaginary flawless server, with no codec history to reconstruct. Source zero and the first picture coincide.

For each of the [24 mark positions](../../../tools/playback-lab/format-matrix.py), capture decoded PCM (`--ao=pcm`) and hash **every** displayed image (`--vo=image`, software decode). Test straight through and fresh starts −200 ms, −1 ms, on the join and +520 ms where applicable: 113 takes including 24 straight. Compare the entire tail through EOF against the source minus the half-open mark. A fresh start may add one identified preceding **kept** sample; compare a two-sample lead against the uncut start at the same picture. A straight cut has no such tolerance. Local files or container loopback only.

- **Stock desktop mpv 0.40, split source-clock WAV:** picture sequences correct, but only **20/24** positions cleared even the cut/start checks (9/24 exactly from the first picture). Interior edges: video −2 µs, audio +10 µs, as in adapter rev 4. At `[10999,15001)` ms, start at 10998 ms: first picture 376, first sound ID **721918**, expected **721920**; the original adds zero. At `[4040,8361)` the straight cut omits the final kept sample. For reproduction, that two-sample witness uses WAV entries `0,10.999010` and `15.001010,4.998990` (start,length seconds). The endpoint cannot cure either failure by providing a better decode.
- **Stock mpv 0.42, separate loopback diagnostic:** synthesize a range-readable WAV of *only* the kept samples, in order, on demand. With one ordinary audio segment, **13/24** positions cleared cuts/starts. Bypassing segment clipping on that single already-safe audio source (`!no_clip`, internal mpv feature) raised it to **22/24**, still two samples early at the same resume. Never disable clipping on the original source. A marked-byte exclusion guarantee is not R21.

- **Real seeks can fail behind a correct picture.** On unpatched mpv master `04b1afc49`, seek while paused from filtered 12.549 s back to 10.999 s for the `[10999,15001)` mark, then unpause. Picture 376 was correct; PCM began at 721918, not 721920. All 18 sequences across four diagnostic marks had correct picture, but 11 sound tails differed. The uncut seek control produced the expected PCM.

These are player diagnostics, **not register rows**. They do not measure an actual Jellyfin decoder, HTTP ticket, multichannel conversion, subtitles, a household remote, the Android mixer or HDMI. A paused landing screenshot is not a sound capture. The existing [format sweep](02-boundary-sweep.md) and R21 admission work supersede single-position observations; the 10.11 R21 sweep reports 0/15 common layouts and only 8/24 positions for the historical MP4 AAC fixture.

## If decoded sound is ever reconsidered

Use the **uncut source clock** first. Bind the response to the exact household ticket, edition, track, clock, format and revision. The EDL alone describes the cut. Changed source, expired ticket, failed read, ambiguous time zero or resource limit: stop. No fallback track or transcode.

- **Bits and clock.** Match the uninterrupted player's decode on every channel, including priming, channel order, precision and output conversion. AAC PNS has a running pseudorandom state; starting the decoder at a nearby packet is not starting it with the right history. Counting samples from an arbitrary MKV block does not recover the original clock or legitimate gaps. Integer FLAC is lossless only relative to its input integers, not necessarily to a decoder's float output.
- **Seeking.** Map WAV/RF64 byte ranges to header bytes and interleaved sample indices. Retries, overlapping ranges and partial samples must return identical bytes with correct length and `206`/`Content-Range`. Decode from origin and discard, or use a restart point *proved* equivalent. A FIFO does not answer a reverse seek: a cold seek near the end can cost hours of source decode. Cap concurrent work, memory and latency; cancel on disconnect. FLAC saves bandwidth but needs its own reproducible byte/seek map.
- **Bandwidth.** At 48 kHz, stereo 16-bit PCM costs 1.536 Mbit/s before video; 5.1 at 24-bit costs 6.912 Mbit/s, or 9.216 Mbit/s at 32-bit. No receiver passthrough. Resampling or downmixing is a different output tuple.
- **Storage and law.** An uncut decoded track is not a concatenated edited soundtrack; neither design requires a saved edited movie. But both make Cleanyfin serve media, contrary to [R01](../../../.claude/41-QUESTIONS-RESOLVED.md) and the [delivery pattern](../../../.claude/13-DELIVERY-PATTERN.md). For a proposed exception, trace what the server, proxy and client can replay after seek, disconnect or crash. Bound and drain buffers; rule out transcode files, disk caches, downloads, swap and dumps. `no-store`, tmpfs or later deletion do not establish non-fixation. The [legal research](../client-ecosystem-2026-11-21/temporary-copies-and-family-movie-act.md) treats a memory-only edited stream as unresolved. Owner scope approval and counsel on actual retention, authorized source, private household direction and notice would precede a product commitment.

## Keep clock, controls and decoder state separate

**Sample membership.** On a continuous track at rate `R` with first sample at `t0`, mark `[a,b)` removes indices **`ceil((a−t0)R)` through `ceil((b−t0)R)−1`**. Compute with rational/integer time. At 48 kHz whole milliseconds already land on samples; `lrint` repairs floating error *at those instants*. At 44.1 kHz, 1 ms lies between samples 44 and 45: nearest is 44, but the first instant at or after the boundary is 45. Refuse unverified clock/rate combinations; rounding is not a universal cut policy.

**Resume.** The plugin can map a filtered bookmark and check a PlaybackInfo start, but Wholphin's D-pad, scrub bar, chapters, rebuffer and restart can seek within an already-open EDL without a new plan. HTTP byte ranges do not identify the intended frame. Even a perfect near-join exclusion zone would leave far-away PNS state and Matroska pre-roll wrong. Do not silently snap or disable controls to earn admission.

**Mute.** A sample-index gate after continuous decoding can zero exactly the marked sound for an audio-only action; the [desktop mute prototype](../playback-paths-2026-11-21/README.md) tested one such case. Compressed passthrough bypasses that gate; a seek can still lose PNS/overlap state. Cutting the picture but leaving a silence of the old duration puts all later sound out of sync. Removing that silence to restore sync is an audio cut again. Stock Wholphin has no demonstrated per-ticket, sample-index mute action. Keep R19 subtitle suppression independent.

**Longer-term:** upstream could decode marked blocks without presenting them and map kept output to the filtered clock. This preserves PNS and overlap state across a cut. A cold seek still needs equivalent state: decode from origin or a proven codec-specific restart point. A Dolby overlap may need only prior packets; AAC noise might need an index of how many random draws preceded the restart, plus overlap pre-roll. Simply choosing a new seed changes the reference. Long skips may stall. This belongs in the existing player, before picture/audio output, not in a Cleanyfin client. It is a bigger feature than `lrint`.

## Next gate and maintenance

1. **Smallest decisive test still open:** A/B the same mpv `04b1afc49` with and without the two `lrint` substitutions, with split WAV audio boundaries **on** integer sample instants (not the old +10 µs). Seek 12.549 → 10.999 s and unpause: require frame 376 and exactly samples 721920…959999. Also run the leading `[0,1001)` cut to EOF. Failure rejects this design; success earns the full 24-position split sweep, not support. The prior rounded standalone repro passed 102/102 audio segments, 82/82 audio-only starts and 59/59 A/V starts; it did **not** test this split. Our paired build timed out in the host queue before launch: the patched all-position answer is **unknown**. Then test unchanged MP4 E-AC-3 with fixed noise generation and verified PNS-free MP4 AAC in its joint partition, through actual Jellyfin tickets. Include PCM after unpausing both seek directions, persisted resume, EOF, every channel and R19. Only expand after a pass.
2. **Upstream chain, not a promised date:** owner files the minimal rounding reproducer → upstream accepts a fix or equivalent → released mpv contains it → Wholphin packages that libmpv/FFmpeg for its Android ABI → measure that app/build in the emulator and on the actual onn. 4K Pro output. No APK fork or Cleanyfin-pinned private client. Record the behavioral test, not just `version >= X`; the patch alone neither seeds AAC nor repairs MKV pre-roll.
3. **[Upkeep ledger](../../../.claude/32-UPKEEP-LEDGER.md) at implementation:** bind Jellyfin/plugin build, adapter/revision, app/APK hash, libmpv and FFmpeg revision/options, original stream/clock/track evidence, backend, device/firmware and negotiated output (decoded PCM, not passthrough) to measured controls/results. Map a known bundled binary to its APK; a self-reported app name is not proof of backend/mode. If the actual mode is indistinguishable at offer time, refuse. Re-run on every changed component, track or output mode; a server-decoder exception would additionally own range regeneration, resource limits, and retention audits. No silent promotion from a desktop result.

**Owner decision:** none needed for the metadata-only experiment or the already-chosen upstream filing. Keep R21 and R01; reopen R01 only if a no-storage household media service becomes an explicit product goal after the native path is measured.

## Result, 2026-11-25: the rounding fix alone does not make the split path exact

**With the `lrint` fix: not exact on the split path.** It removes one of the two early samples, not both. The design's next gate failed, so the 24-position sweep was not run.

Same mpv master `04b1afc49` (Arch build, FFmpeg from Arch), built twice from one tree: unchanged, and with only the two `lrint` substitutions in `mp_aframe_clip_timestamps`. Same fixture and oracle as the counter test above (500 distinct pictures, sample *n* = *n*). The split EDL puts sound boundaries **on** whole-sample instants (`a.wav,0,10.999` and `a.wav,15.001,4.999`; picture −2 µs). Local files, `--network none`. Positive control on both builds: uncut seek 15 → 12 s, picture 300, PCM from 576000 exactly.

| Take, mark `[10999,15001)` ms | Unchanged master | With `lrint` |
|---|---|---|
| Paused seek 12.549 → 10.999 s, unpause | picture 376 ✓, PCM from **721918** (2 early) | picture 376 ✓, PCM from **721919** (1 early) |
| Paused seek 12.549 → 10.9977 s | 721918 | 721919 |
| Paused seek 0.5 → 11.9997 s (picture 400) | 767998 (2 early) | **767998 (still 2 early)** |
| Paused seeks to 10.9577 s and 0.5 s | exact | exact |
| Fresh starts 10.998 / 10.999 / 11.519 s | 2 early: fails R21 | 1 preceding kept sample: R21 tolerance |

| Take, mark `[0,1001)` (cut from time zero) | Unchanged master | With `lrint` |
|---|---|---|
| Straight play from the start | first picture 26 ✓, PCM from **49919**, expected 49920 | **same: 49919** |
| Fresh starts 0 and 0.52 s | 1 early (tolerated) | 1 early (tolerated) |
| Paused seeks 1.55 → 0, 0.5 → 1.0007, 0.5 → 0.5 s | 1 early each | 1 early each |

Every early sample is a **kept** sample immediately before the landing picture. No marked sample and no wrong picture appeared on either build. That is still not R21. A straight cut has no tolerance, and a seek inside an open EDL must land exactly. The remaining one- and two-sample offsets survive the rounding change. The likeliest source, not yet located, is how mpv aligns sound to the landing picture on a seek and at the first picture. `lrint` is a correct upstream fix for its own reproducer. It is not enough for Cleanyfin's split cut list.

Consequences:

- **Design gate:** "failure rejects this design" applies to *rounding as the whole fix*. Split EDL on original tracks with a stock or rounded mpv stays refused. The upstream rounding report remains worth filing on its own evidence; do not cite it as making Cleanyfin exact.
- **Not measured, so unchanged:** the 24-position MP4 E-AC-3 (with and without `mov_text`) and PNS-free MP4 AAC sweeps (gated on this pass); MKV E-AC-3 decoder-history and pre-roll failures (`issue-2`, untouched by this patch); AAC PNS generator state; Jellyfin tickets, Wholphin/libmpv and devices.
- **What could next distinguish causes:** locate where mpv chooses the audio start after an hr-seek inside an EDL segment (the 2-sample case at 11.9997 s and the 1-sample time-zero case), before any new player build or sweep.

Images kept for re-runs: `cleanyfin-mpv:master` (unchanged) and `cleanyfin-mpv:master-lrint`. Scripts and JSON: `~/.cache/cleanyfin-scratch/lrint-experiment/` (`spec-decisive/`, `results/`).
