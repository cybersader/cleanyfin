# Marked-sound detector v2 (R24 test tier)

**2026-11-26 · desktop mpv lab only.** This page explains how the R24 test tier now decides "no marked sound was presented" after lossy AC-3/E-AC-3 decoding. It also explains how small a leak the test can catch, and what the re-run sweep found. It does not qualify any TV, Wholphin, HDMI or passthrough path.

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
- **Codec spread is not a presented marked frame, and is reported.** After a decoder restart with no history (Matroska joins), up to one half-block (256 frames, 5.3 ms) of kept output is altered. MDCT time-domain aliasing means the altered frames can carry a windowed, time-reversed trace of source material from the same half-block, which may lie inside the mark. Measured at `[3039, 7999)`: 48 altered kept frames, at most 706 LSB (vector norm), and at least 507 LSB from any marked frame. Across the whole sweep: up to 256 altered kept frames per take, at most 21,759 LSB, and never closer than 296 LSB to a decoded marked frame. Receipts record `alteredKept` and `maxAlteredLSB` per take. R24 v2 does not decide whether that trace is inaudible; the R25 projection and threshold below are a separate check.

## Proof on controls

Every position also runs these checks:

- **Ideal cut:** the uncut decode with exactly the marked frames removed must give zero alarms and every frame exact.
- **Injected positives in every scored take:** these use 1 to 8 marked frames. Injections are before-join overrun, after-join early start (exact, and altered by the take's own post-join deviation or ±300 LSB), a join shift, early starts for takes that begin after the mark, end overrun for marks to the end, and interior inserts, substitutions and near-substitutions within τ/2. Every one must be detected; one miss makes the position not clear. The modified arrays exist only in memory and are never stored as media.
- **Held-out histories and decoders, per layout:** uncut fresh starts at 2.0, 7.37 and 13.013 s, an uncut paused seek-resume at 9.5 s, and decodes by Jellyfin 10.11's ffmpeg 7.1.4 and 12.4's ffmpeg 8.1.3 (straight and from 7.37 s). Each is judged against all 24 marks. The truth comes from an independent cross-correlation alignment. Counts are reported per frame: true positives, false negatives, false positives and true negatives.

Local dry run (plain files, the renderer's Python mirror, Matroska E-AC-3, all 24 positions, 341 s): 24/24 clear, no injected positive missed. Controls gave 13,393,599 marked frames detected out of 13,393,599 presented, with zero false positives. Results through the plugin are below.

## R24 v2 desktop results

**All 8 Dolby layouts passed the R24 v2 frame-identity and marked-value checks on Jellyfin 10.11.11 and 12.4.0, with every planned take scored at all 24 positions.** R25 separately checks marked-correlated transform spread. That is 2,192 of 2,192 takes and 2,576 paused seek landings. The presented frames contained 0 marked pictures and 0 marked sound frames: 1,359,286,926 exact kept, 10,896 decoder-altered kept identified by position, 220 decoder lead-in, and no unidentified frame.

| Measure | Count (both lines) |
|---|---|
| Injected positives detected (sensitivity, takes) | **199,840 / 199,840**; 0 missed, 0 refused. In 199,504 the count was exactly the number injected; the rest over-counted (join shifts) |
| Held-out controls, marked frames detected (per-frame sensitivity) | **214,375,408 / 214,375,408** (0 false negatives) |
| Held-out controls, kept frames not alarmed (per-frame specificity) | **1,870,420,112 / 1,870,420,112** (0 false positives) |
| Ideal cuts | 384 / 384 with zero alarms, every frame exact |
| Altered kept frames, nearest decoded marked frame | at least 296 LSB away; τ was 30.8–64 LSB; value detections 0 |
| Packets with no exact frame | 16, all resolved decisively (first-packet one-frame resumes) |

Plain per-layout table. Positions: clear out of 24. Takes: scored out of planned. Marked: pictures (filtered takes and paused landings) / sound frames. Positives: detected out of injected. Controls: true positives / false negatives / false positives over all held-out playbacks and marks. Altered: decoder-altered kept frames in all takes (largest vector deviation in LSB). Join loss: largest number of kept frames skipped at a join (reported seam, not gating; 128 frames = 2.7 ms). The two lines gave identical counts per layout, since the server is not in the decode path.

| Layout | Line | Positions | Takes | Marked | Positives | Controls TP / FN / FP | Altered | Join loss | R24/R25 reviewed test rows |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `mkv-h264-ac351` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,464/12,464 | 13,405,887 / 0 / 0 | 353 (≤20,419) | 0¹ | **withheld on both (R25)** |
| `mkv-h264-eac351` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,512/12,512 | 13,393,599 / 0 / 0 | 353 (≤21,759) | 0¹ | **withheld on both (R25)** |
| `mkv-hevc10-eac351` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,512/12,512 | 13,393,599 / 0 / 0 | 353 (≤21,759) | 0¹ | **withheld on both (R25)** |
| `mkv-hevc10-hdr10-eac351` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,512/12,512 | 13,393,599 / 0 / 0 | 353 (≤21,759) | 0 | **withheld on both (R25)** |
| `mp4-h264-eac351` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,464/12,464 | 13,403,839 / 0 / 0 | 257 (≤21,759) | 0 | **desktop test only, both lines** |
| `mkv-h264-eac351+aac-srt` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,496/12,496 | 13,399,743 / 0 / 0 | 1,121 (≤21,759) | 80 | **withheld on both (R25)** |
| `mkv-h264-eac351-ass+srt` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,496/12,496 | 13,393,599 / 0 / 0 | 2,401 (≤21,759) | 128 | **withheld on both (R25)** |
| `mp4-h264-eac351-mov_text` | 10.11 / 12.4 | 24/24 | 137/137 | 0 / 0 | 12,464/12,464 | 13,403,839 / 0 / 0 | 257 (≤21,759) | 0 | **desktop test only, both lines** |

¹ Join loss was added after these three layouts were measured on 10.11; it is from 12.4.

The [reviewed v2 register](../../../delivery/evidence/register-v2.json) contains the four MP4 tuples only, as experimental test playback. This path is off by default. The R22/R23 full tier is untouched. Seam measures (R22 silence, transient, picture, sync and loss fields per take) are in the receipts and are reported, not gating. The Matroska subtitle layouts skip up to 128 kept frames (2.7 ms) at some joins, and decoder restarts alter up to 256 kept frames per take.

**How the run went.** On 10.11 the first job was stopped by the agent's 30-minute background limit after three layouts. Layout four, the five remaining layouts and then two re-runs completed in separate jobs on the same plugin build. The two Matroska subtitle layouts were re-run on both lines after two harness fixes found during the run. First, early-start positives now continue the run that actually played; that run started 128 frames after the mark, and the old positives gave 72 false misses. Second, the R19 cue check now uses one file clock; 12.4's server anchor plus the lab probe's zero had put marks 5 ms early, leaving seven positions unmeasured. Neither fix changes how a presented frame is judged. The merged receipts keep each layout's own fixture hash (Matroska output carries a random segment ID) and harness hashes (`mergedFrom`). Plugin SHA-256 `7cc42c15…` on 10.11 and `05bb2afb…` on 12.4 (one build per line, unchanged across its jobs); mpv 0.40.0 image `dd6013a5…`. Receipts: [10.11.11](../../../tools/playback-lab/prototypes/common-formats/receipt-r24v2-10.11-2026-11-26.json.gz), [12.4.0](../../../tools/playback-lab/prototypes/common-formats/receipt-r24v2-12.4-2026-11-26.json.gz). The v1 partial receipt remains as history.

**Coverage gaps from v1, closed or classified:** "landing or end not witnessed" (12) is now reported as a landing seam; a take still has to reach the end of the file. "No unique source anchor for packet" (12) is resolved by run position. "Positive control unverified" (10) and "outside calibration" (16) are gone, because v2 needs no extra calibration captures. "mpv capture exited 2" (2) was the renderer bug below, now fixed; a player exit is retried once and recorded. "No filtered offer (UnsupportedSubtitle)" (4) did not recur with this build; such a position would be counted as a refused offer, never a pass.

## R25: Matroska restart echo

**All six Matroska Dolby layouts stay out of the test tier on both lines.** The strongest marked-correlated echo was **−39.90 dBFS peak and −51.65 dBFS RMS**, **26.56 dB below** the co-located kept peak and **32.96 dB below** kept RMS, in the right-surround channel. The least-masked RMS on any channel was **32.81 dB below** kept RMS (centre channel). All of these are at the same source interval, `[3039, 7999) ms`. That is above R25's conservative screen on both scales; it does not prove a person will hear the echo in every program or at every volume.

[R25](../../../.claude/41-QUESTIONS-RESOLVED.md) requires both peak and RMS at or below −90 dBFS and 60 dB below same-channel, same-window kept sound. The absolute screen assumes full scale at most 80 dB SPL at the listener, combined across speakers. A missing bound cannot earn the exception.

**Measurement.** `r25-sweep.py` covers the 24 positions and all 137 takes per layout and line. Across all eight layouts on both lines, byte-exact kept PCM in 2,114 takes already proves zero *restart delta* against the uninterrupted decode; 78 takes with altered kept samples were recaptured through the real plugin and pinned desktop mpv. For each replay, the archived and new presented length, first/last source identity, altered frame count and maximum six-channel residual norm agreed. Of the 1,644 Matroska takes, 70 were replayed and 24 had altered output with a reflection in the mark. End cuts and any take with no post-mark support do not silently count as an echo observation.

The `r25-mdct-projection-v1` estimator first subtracts the uninterrupted decode at the same kept sample positions. Then it projects that residual onto the known original marked code reflected within the same 256-sample Dolby half-block, weighted by the decoder's Kaiser–Bessel-derived window. In-place kept attenuation and the reflected *kept* code are separate terms, not attributed to the mark. The transform grid is fixed by the fixture, not chosen to maximize correlation. Only the four independent full-band code channels identify the reflection; the tone and LFE do not. RMS uses just the marked-reflection support, not the full film. This isolates the incremental restart effect, not spread already in the uninterrupted codec decode. A null fitted component would not be sufficient to certify inaudibility of arbitrary tracks. [Numerical receipt](../../../tools/playback-lab/prototypes/common-formats/receipt-r25-echo-2026-11-26.json.gz) records every take and channel fit; no PCM is in that receipt.

| Matroska layout(s) | Lines | Takes with a marked-correlated echo per layout and line | Highest peak / RMS dBFS | Least-masked peak / RMS vs. kept |
|---|---|---:|---:|---:|
| H.264 / AC-3 | 10.11.11, 12.4.0 | 2 | −41.45 / −53.20 | −28.36 / −33.90 dB |
| Five E-AC-3 layouts (see exact names above) | 10.11.11, 12.4.0 | 2 each | −39.90 / −51.65 | −26.56 / −32.81 dB |

**MP4 is different.** For each plain and `mov_text` E-AC-3 tuple, all straight/fresh takes were identical to the kept portions of the uninterrupted decode. Two seek/resume takes had altered kept samples: a single frame before the join at `[1600, 3200)`, and 256 frames of a new half-block beginning 896 samples *after* the mark ending at 1000 ms. Neither altered window carries a reflection from that mark. This is not a claim that MP4 never restarts after seeking. It establishes no history-less *Matroska join* contamination in the measured MP4 tuples, with the ordinary seam differences still reported in the four reviewed rows.

**Listening comparison.** `r25-listen.py` encodes original synthetic speech and music, plays the worst Matroska join through the pinned plugin and player, and exports loudness-matched ideal and actual cuts. The ideal joins the kept slices of the uninterrupted decode. The amplified echo-only clip is a controlled difference: repeat with *only* the mark's source polarity inverted and take half the difference between the two aligned restart deltas. It is the short marked-dependent response, not a played marked frame, and includes any codec nonlinearity affected by the control. An envelope PNG shows kept versus isolated response at original gain. The straight-play E-AC-3 example on 10.11.11 ties the worst masking ratio on both lines; the exported stereo A/B files both measure −28.6 LUFS and the echo-only clip uses 27.36 dB of gain. All of the clips are synthetic demonstrations outside version control, not admission evidence or household media.

## Renderer fix found by this work

A mark that starts on the first picture of a Matroska Dolby file left a kept range holding only the 5 ms of sound priming before file time 0. The renderer wrote it as a negative picture segment, and mpv refused the whole cut list (v1's "mpv capture exited 2"). `MpvEdlRenderer.FileSegments` now drops a kept range that ends at or before file time 0 from every partition. The output is then identical to a mark from source 0 (Delivery contract tests: 211/211).

## What this does not prove

Desktop mpv 0.40 in the lab container only, synthetic 20 s fixtures, decoded PCM. Identity relies on deterministic decoding (`cons_noisegen=1`); without it the held-out ffmpeg decoders were 0% exact. Nothing here measures a TV, Wholphin, HDMI output, passthrough, real encoder settings, other sample rates, or what a person hears.
