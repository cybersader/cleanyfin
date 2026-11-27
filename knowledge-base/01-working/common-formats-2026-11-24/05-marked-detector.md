# Marked-sound detector v2 (R24 test tier)

**2026-11-26 · two different witnesses.** The desktop R24 sweep measured six-channel PCM at mpv's output. The TV harness instead identifies source positions in Android's recorded stereo mix. Its sensitivity figures below come from offline challenges, not from a TV playing a movie. Neither the desktop results nor the TV harness implementation qualify Wholphin, HDMI or passthrough.

## Android stereo playback mix

The TV test measures **what was in the recording**, not what a decoder timestamp says should have been there. The desktop method below still owns sample-level, six-channel identity. The TV method has a different fixture, resolution and refusal policy. Neither one certifies what comes out of an HDMI receiver or speaker.

**Signal.** The original 20 s barcoded movie is unchanged in length, codec, channel layout and video. `tv-spread-v1` puts one deterministic aperiodic waveform (linear interpolation between random 6 kHz chips) at different positive levels in all five full-range lanes; LFE is silent. That preserves the position code instead of cancelling it during a 5.1→stereo fold. No mark is baked into the code. Decode this edition uncut with the verified Dolby clock, **including** any prefix before picture zero. Fold its six float lanes using the previously observed Android mix as a hypothesis, never as evidence about this TV. Two same-route uncut captures must independently recover the full programme. For each mark, reclassify a known marked block from the first capture and a kept block from the other. No thresholds are trained on the filtered takes.

**Reading a block.** On each ear independently, query a fingerprint index and verify candidates against the entire 240-sample waveform. Fit positive gain and a bounded local sampling phase/rate, not arbitrary equalization; check the source and the one legal splice hypothesis. Overlap windows every 120 samples and anchor the last window at the very end. A verified window wholly inside the mark in either ear fails the take. A negative requires both ears to cover the kept path, with its witnessed entry and end and no timing holes. The complete compact position maps are in `traceDeltaZlibLR`: base64, zlib, little-endian int32 deltas, cumulative sum. Each entry is a kept-path sample index, `-1` (unknown), or `-(sourceIndex+2)` (marked). `sourceMarkFrames`, `activeFrames`, and the zero-only recorder padding locate the map on both clocks. No edited movie or PCM is retained.

**Two distinct results.** `blockOutcome=pass` means every active window was identified on the kept path. `outcome=pass` additionally requires that no sample differs by more than one captured s16 count from its fitted kept waveform. It is not enough to recognize the louder sound if a quieter echo is still unexplained. One count is a digital measurement limit, **not a hearing threshold**. Saturation, an incompatible fold, uncertain clocks, unknown pauses/seeks, incomplete route/transfer, missing EOF or unresolved energy can leave clearance unmeasured. The full gate remains closed if any required take or mark is unresolved. In particular the 1 ms marks are below this test's negative-verdict floor. This test does not measure R22 pop, lost kept sound, or A/V sync, and the common code cannot establish six independent channel identities.

**Offline challenge.** `tv-sound-validation.py` uses the pinned lab FFmpeg image to encode/decode full-length AC-3 and E-AC-3, then independently folds the six decoded lanes with a second matrix and different L/R levels, quantizes to s16, and varies the sample clock by ±400 ppm. It tests starts, arbitrary interior replacements, join replacements, ends, and under-cuts contiguous with either flank. The numbers refer to these finite synthetic challenges; overlapping windows are not independent trials. A 5 ms analysis window is **not** a 5 ms guaranteed detection limit. A complete box run still needs its own evidence.

**Reporting floor: 10 ms (480 frames at 48 kHz), for substitutions of the marked programme at the tested levels.** At each duration and for each codec the [frozen challenge](../../../tools/playback-lab/prototypes/common-formats/tv-spread-v1-offline-2026-11-26.json) used 12 placements of each of six kinds. The table counts takes with a **confirmed marked block**; other takes abstained, none cleared. The injected takes are excerpts, not full movies, so abstentions on these do not measure the false-clear probability of a complete movie. These numbers do not establish sensitivity to an arbitrary low-level echo.

| Injected duration | AC-3 confirmed / 72 | E-AC-3 confirmed / 72 |
|---|---:|---:|
| 1 ms | 0 | 0 |
| 2.5 ms | 0 | 0 |
| 5 ms | 25 | 23 |
| 7.5 ms | 72 | 72 |
| 10 ms | 72 | 72 |
| 20 ms | 72 | 72 |

The [earlier four-placement challenge](../../../tools/playback-lab/prototypes/common-formats/tv-spread-development-offline-2026-11-26.json) missed two 7.5 ms substitutions per codec (46/48 for each), while confirming 48/48 at both 10 and 20 ms for each. Its whole-take endpoint checks also abstained; it is not the passing validation receipt. The fresh result does not lower the predeclared 10 ms floor.

**Specificity on the frozen near-model folds:** all ten full kept takes cleared, with **0 false alarms and 0 unknowns in 52,490 overlapping stereo windows**. Each codec supplied five complete takes: the ideal cut, independently rounded fold with unequal ear gains at nominal rate and ±400 ppm, and FFmpeg's default stereo decode. The largest kept residual was 0.771 s16 counts. The alternative coefficients in this battery are close to the reference fold (`0.707` versus `1/√2`); these results do not establish clearance under an arbitrary TV downmix.

**Actual muxed fixtures, still offline.** The [MKV AC-3 and MP4 E-AC-3 check](../../../tools/playback-lab/prototypes/common-formats/tv-spread-v1-muxed-2026-11-26.json) used the TV harness's own fixture generator and reference reader. Decoded pre-roll was 240 and 0 frames respectively. Both ideal cuts, all four generated uncut controls and both per-mark positive/negative challenges passed. With a substantially different channel fold (centre 0.50, surround 0.35, no LFE; independent ear gains and ±400 ppm), **none of four clean cuts cleared**. There were 0 false alarms in 23,930 overlapping windows, but two AC-3 windows were unidentified, and residuals in all four takes exceeded one count (worst: 141.226). E-AC-3 identified every position in both altered-fold takes; a position match alone would have incorrectly claimed unexplained sound was clear. An unfamiliar real route may likewise abstain.

## Why the v1 detector could not decide

v1 built a neighbourhood of decoded *marked* sample vectors and called any presented vector inside it marked. On the tone fixture every channel carries the same smooth waveform family at different levels. Decoded marked and kept vectors therefore lie on nearly the same curve. On 12 desktop receipts (1,644 planned takes) it fired on byte-exact kept PCM in 718 takes (1,847 sample occurrences). 16 positions stopped with "outside calibration", and 172 takes were never scored. No marked frame and no source-traced marked sample was seen, but a detector that cannot tell kept from marked proves nothing.

## The signature: a source-time code

`format_fixtures.generate(..., signature='timecode')` (5.1 Dolby default tracks only) keeps the picture, packets and layout. Channel 0 keeps the 440 Hz clock tone used to place the source clock, and the LFE keeps its tone. Each of the other four full-band channels carries an independent deterministic pseudo-random code (splitmix64, ±0.2 full scale). Every sample frame of the decoded uncut file is then a distinct 96-bit value: all 958,210 frames of the Matroska E-AC-3 decode are unique, and the harness checks this on every decode it uses. The code's own cross-correlation must also put the source clock where the tone does (measured lag 2: correlation 0.89 against 0.17 one sample either side). Marks move to 24 positions on one file, so one code identifies every position. A marked-only code baked into the file would need a new file per position.

## How each presented frame is identified (`marked_oracle.py`)

1. **Exact identity.** A presented frame equal, bit for bit on all six channels, to a unique frame of the uncut decode *is* that source position. This works one frame at a time with no noise, because AC-3/E-AC-3 decoding with `cons_noisegen=1` is deterministic for the same decoder history.
2. **Position, for frames a decoder restart altered.** mpv's audio filter packets each come from one decoder run, and frames within a run are contiguous. A packet with any exact frame gives its other frames the lag of the nearest exact frame. A packet with no exact frame is placed by the previous run, the next run, or its own nearest-value alignment, whichever leaves the least residual energy. Where these candidates disagree about markedness, the winner must have at most a quarter of the runner-up's energy, or the take is unmeasured. A take's first packet is never assumed to belong to the run after it: on a Matroska resume the first packet was one frame holding the last kept frame before the join, 2.8 LSB from exact.
3. **Value check.** Every frame that is not exact is also compared with all decoded marked frames. One within τ is a detection even if its position says kept. τ is at most 64 LSB and at most half the fixture's smallest kept-to-marked distance for that mark (32.8–64 LSB in practice), fixed before any take is read.

A frame is **marked** if it is exactly marked, positionally marked or within τ of a marked frame. A take is scored only if the audio packet trace covers every byte and both picture and sound reach the end of the file (or the frame before a mark that runs to the end). A take that cannot be identified is unmeasured, never clear.

## How small a leak it can catch

- **Per-sample value identity against the source is impossible on these codecs.** AC-3 at 448 kb/s carries about 1.6 bits per sample per channel (448,000 / 48,000 / 6), E-AC-3 at 640 kb/s about 2.2. The code channels come back at about 6 dB SNR (correlation 0.89). Comparing a decoded frame with the *source* cannot tell one sample from its neighbour.
- **Decode-to-decode identity is exact:** the leak unit is **one sample frame (about 21 µs)** on both codecs, for every leak mpv can produce. mpv presents contiguous runs of decoder output clipped at boundaries, so leaks are a run that ends late, starts early, overruns the end of the file or resumes early. Each of those is resolved either by exact identity or by the position of a run anchored on exact frames.
- **The limit is non-physical substitution inside a run.** A marked value that replaces a kept frame inside a decoder-altered stretch is caught only if it lies within τ of the decoded marked value. mpv has no mechanism that produces this; the bound is stated rather than hidden.
- **Codec spread is not a presented marked frame, and is reported.** After a decoder restart with no history (Matroska joins), up to one half-block (256 frames, 5.3 ms) of kept output is altered. MDCT time-domain aliasing means the altered frames can carry a windowed, time-reversed trace of source material from the same half-block, which may lie inside the mark. Measured at `[3039, 7999)`: 48 altered kept frames, at most 706 LSB (vector norm), and at least 507 LSB from any marked frame. Across the whole sweep: up to 256 altered kept frames per take, at most 21,759 LSB, and never closer than 296 LSB to a decoded marked frame. Receipts record `alteredKept` and `maxAlteredLSB` per take. Whether that trace should count as marked is a question for the owner, not something this test decides.

## Proof on controls

Every position also runs these checks:

- **Ideal cut:** the uncut decode with exactly the marked frames removed must give zero alarms and every frame exact.
- **Injected positives in every scored take:** these use 1 to 8 marked frames. Injections are before-join overrun, after-join early start (exact, and altered by the take's own post-join deviation or ±300 LSB), a join shift, early starts for takes that begin after the mark, end overrun for marks to the end, and interior inserts, substitutions and near-substitutions within τ/2. Every one must be detected; one miss makes the position not clear. The modified arrays exist only in memory and are never stored as media.
- **Held-out histories and decoders, per layout:** uncut fresh starts at 2.0, 7.37 and 13.013 s, an uncut paused seek-resume at 9.5 s, and decodes by Jellyfin 10.11's ffmpeg 7.1.4 and 12.4's ffmpeg 8.1.3 (straight and from 7.37 s). Each is judged against all 24 marks. The truth comes from an independent cross-correlation alignment. Counts are reported per frame: true positives, false negatives, false positives and true negatives.

Local dry run (plain files, the renderer's Python mirror, Matroska E-AC-3, all 24 positions, 341 s): 24/24 clear, no injected positive missed. Controls gave 13,393,599 marked frames detected out of 13,393,599 presented, with zero false positives. Results through the plugin are below.

## R24 v2 desktop results

**All 8 Dolby layouts meet the R24 gate on both Jellyfin 10.11.11 and 12.4.0: zero marked material at every one of the 24 positions, with every planned take scored.** That is 2,192 of 2,192 takes and 2,576 paused seek landings. The presented frames contained 0 marked pictures and 0 marked sound frames: 1,359,286,926 exact kept, 10,896 decoder-altered kept identified by position, 220 decoder lead-in, and no unidentified frame.

| Measure | Count (both lines) |
|---|---|
| Injected positives detected (sensitivity, takes) | **199,840 / 199,840**; 0 missed, 0 refused. In 199,504 the count was exactly the number injected; the rest over-counted (join shifts) |
| Held-out controls, marked frames detected (per-frame sensitivity) | **214,375,408 / 214,375,408** (0 false negatives) |
| Held-out controls, kept frames not alarmed (per-frame specificity) | **1,870,420,112 / 1,870,420,112** (0 false positives) |
| Ideal cuts | 384 / 384 with zero alarms, every frame exact |
| Altered kept frames, nearest decoded marked frame | at least 296 LSB away; τ was 30.8–64 LSB; value detections 0 |
| Packets with no exact frame | 16, all resolved decisively (first-packet one-frame resumes) |

Plain per-layout table. Positions: clear out of 24. Takes: scored out of planned. Marked: pictures (filtered takes and paused landings) / sound frames. Positives: detected out of injected. Controls: true positives / false negatives / false positives over all held-out playbacks and marks. Altered: decoder-altered kept frames in all takes (largest vector deviation in LSB). Join loss: largest number of kept frames skipped at a join (reported seam, not gating; 128 frames = 2.7 ms). The two lines gave identical counts per layout, since the server is not in the decode path.

| Layout | Line | Positions | Takes | Marked | Positives | Controls TP / FN / FP | Altered | Join loss | R24 test tier |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `mkv-h264-ac351` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,464/12,464 | 13,405,887 / 0 / 0 | 353 (≤20,419) | 0¹ | **admitted on both** |
| `mkv-h264-eac351` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,512/12,512 | 13,393,599 / 0 / 0 | 353 (≤21,759) | 0¹ | **admitted on both** |
| `mkv-hevc10-eac351` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,512/12,512 | 13,393,599 / 0 / 0 | 353 (≤21,759) | 0¹ | **admitted on both** |
| `mkv-hevc10-hdr10-eac351` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,512/12,512 | 13,393,599 / 0 / 0 | 353 (≤21,759) | 0 | **admitted on both** |
| `mp4-h264-eac351` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,464/12,464 | 13,403,839 / 0 / 0 | 257 (≤21,759) | 0 | **admitted on both** |
| `mkv-h264-eac351+aac-srt` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,496/12,496 | 13,399,743 / 0 / 0 | 1,121 (≤21,759) | 80 | **admitted on both** |
| `mkv-h264-eac351-ass+srt` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,496/12,496 | 13,393,599 / 0 / 0 | 2,401 (≤21,759) | 128 | **admitted on both** |
| `mp4-h264-eac351-mov_text` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,464/12,464 | 13,403,839 / 0 / 0 | 257 (≤21,759) | 0 | **admitted on both** |

¹ Join loss was added after these three layouts were measured on 10.11; it is from 12.4.

What "admitted" means here: the measured desktop-mpv tuple meets the R24 test-tier gate. The reviewed register (`delivery/evidence/register-v2.json`) is unchanged. Installing test-tier rows is a reviewed step for the owner, and the R22/R23 full tier is untouched. Seam measures (R22 silence, transient, picture, sync and loss fields per take) are in the receipts and are reported, not gating. The Matroska subtitle layouts skip up to 128 kept frames (2.7 ms) at some joins, and decoder restarts alter up to 256 kept frames per take.

**How the run went.** On 10.11 the first job was stopped by the agent's 30-minute background limit after three layouts. Layout four, the five remaining layouts and then two re-runs completed in separate jobs on the same plugin build. The two Matroska subtitle layouts were re-run on both lines after two harness fixes found during the run. First, early-start positives now continue the run that actually played; that run started 128 frames after the mark, and the old positives gave 72 false misses. Second, the R19 cue check now uses one file clock; 12.4's server anchor plus the lab probe's zero had put marks 5 ms early, leaving seven positions unmeasured. Neither fix changes how a presented frame is judged. The merged receipts keep each layout's own fixture hash (Matroska output carries a random segment ID) and harness hashes (`mergedFrom`). Plugin SHA-256 `7cc42c15…` on 10.11 and `05bb2afb…` on 12.4 (one build per line, unchanged across its jobs); mpv 0.40.0 image `dd6013a5…`. Receipts: [10.11.11](../../../tools/playback-lab/prototypes/common-formats/receipt-r24v2-10.11-2026-11-26.json.gz), [12.4.0](../../../tools/playback-lab/prototypes/common-formats/receipt-r24v2-12.4-2026-11-26.json.gz). The v1 partial receipt remains as history.

**Coverage gaps from v1, closed or classified:** "landing or end not witnessed" (12) is now reported as a landing seam; a take still has to reach the end of the file. "No unique source anchor for packet" (12) is resolved by run position. "Positive control unverified" (10) and "outside calibration" (16) are gone, because v2 needs no extra calibration captures. "mpv capture exited 2" (2) was the renderer bug below, now fixed; a player exit is retried once and recorded. "No filtered offer (UnsupportedSubtitle)" (4) did not recur with this build; such a position would be counted as a refused offer, never a pass.

## Renderer fix found by this work

A mark that starts on the first picture of a Matroska Dolby file left a kept range holding only the 5 ms of sound priming before file time 0. The renderer wrote it as a negative picture segment, and mpv refused the whole cut list (v1's "mpv capture exited 2"). `MpvEdlRenderer.FileSegments` now drops a kept range that ends at or before file time 0 from every partition. The output is then identical to a mark from source 0 (Delivery contract tests: 211/211).

## What this does not prove

Desktop mpv 0.40 in the lab container only, synthetic 20 s fixtures, decoded PCM. Identity relies on deterministic decoding (`cons_noisegen=1`); without it the held-out ffmpeg decoders were 0% exact. Nothing here measures a TV, Wholphin, HDMI output, passthrough, real encoder settings, other sample rates, or what a person hears.
