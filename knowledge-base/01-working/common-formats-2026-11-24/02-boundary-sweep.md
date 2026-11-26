# Cut-position admission for desktop mpv

**2026-11-25 · desktop mpv lab only.** The split cut-list renderer is unchanged. R22 replaces sample-exact kept sound as the admission bar; zero marked material is still absolute. No experiment on this page qualifies a TV, Wholphin, HDMI or passthrough path.

## R24 test tier: marked-sound detector v2 (2026-11-26)

The R24 opt-in test tier gates only on marked material and reports seam measures. Detector v2 identifies the source position of every presented sample frame after lossy decoding (exact identity on a source-time code fixture, then packet-run position for altered frames). Each take must also catch injected marked positives down to one frame. R25 additionally requires an inaudibility bound for marked-correlated restart smear. Only the plain and `mov_text` MP4 E-AC-3 layouts earned desktop test rows on both lines; Matroska Dolby did not. See the [detectors, controls, and per-layout results](05-marked-detector.md). Full-tier admission under R22/R23 below is unchanged.

## R22 viewer rule, amended by R23

[R22](../../../.claude/41-QUESTIONS-RESOLVED.md) requires every straight cut, fresh start and seek-and-resume at the 24 positions to satisfy six checks. Only Dolby can be admitted, and only as `experimental` for the exact tested desktop-mpv tuple. FLAC and AAC measurements are diagnostic only; both remain refused. Every fixture sharing a tuple must pass before that tuple can be admitted.

| Check | Lab measurement and limit |
|---|---|
| Nothing marked | Zero marked barcodes, including paused landings; zero marked samples. PCM must have positive all-channel source identity on the uncut source clock. An ambiguous or unidentified sample refuses. The nearest-mark waveform residual is diagnostic only: synthetic substitution does not calibrate alternate codec histories. |
| No added silence | Compare longest all-channel runs at digital zero and below −60 dBFS against the ideal hard cut, within ±100 ms of the splice. No extra sample at either floor. |
| No added pop | Second-order 2 kHz high-pass; 5 ms RMS channel by channel, centred on a splice or one-sided at an onset/endpoint. A splice cannot exceed the ideal hard cut plus the largest same-channel 5 ms delta measured between two same-clock uncut captures. Fresh starts and seek-resumes also compare the onset to an uncut start or seek-resume at the same source picture: allowed excess is the uncut onset's excess over its ideal plus that measured repeat noise. A take that plays through a splice must pass both comparisons. The receipt includes the actual, ideal, uncut comparator, noise and margin for each applicable comparison. |
| Picture intact | Every kept barcode once, in order from the expected landing; timing can differ by at most 1 ms beyond the layout's uncut picture jitter. |
| A/V in sync | Compare content-anchored audio packet PTS with the video timeline and uncut offset. Ordinary jitter is the measured uncut envelope plus 1 ms timestamp resolution; a control wider than 20 ms is unmeasured. Playback must have an observed in-jitter suffix within 100 ms and stay there. |
| Kept sound loss | At most 1,920 kept sample frames (40 ms at 48 kHz) absent from the requested playback. Count unique source coverage; extra samples cannot offset a drop. |

The test taps audio/video media timestamps in the same desktop-mpv take as the PCM and picture capture. Complete audio packets are matched to capture bytes by checksum, not by assuming that the WAV's concatenated sample index is a clock. The ideal cut is the same uncut decode with the marked indices removed. This measures media-output timing, not latency to speakers or a display. Missing traces, timeouts and partial sweeps never qualify a layout.

[R23](../../../.claude/41-QUESTIONS-RESOLVED.md) amends the onset comparison only when the fresh-start or in-session resume target is the join. Drop the first sample frame (all channels, about 21 µs) from the original 5 ms window **before** applying the high-pass filter. Apply the same omission to the ideal and the matched uncut onset controls; keep the repeat-noise threshold unchanged. Keep that first sample in the marked-material proof, silence, splice, kept-sound and clock checks. At most one extra kept sample can precede the landing relative to the uncut control. Offset starts (including 200 ms or 1 ms before and 520 ms after) and straight playback do not get the exception.

## R22/R23 results

**Dolby admission: 0 of 8 layouts on Jellyfin 10.11.11; 0 of 8 on 12.4.0.** The reviewed register is unchanged. On 10.11.11 all 336 positions were attempted under R22. The two-hour outer job limit stopped the first 12.4.0 run after the first two layouts; the interrupted HEVC Main 10 attempt is unmeasured. Both completed layouts had independent measured failures on criteria R23 did not change. The recovery remeasured the six missing Dolby layouts under frozen R23, completing the 192 Dolby positions on 12.4.0. The two captures on 12.4.0 used identical plugin and player builds. HEVC Main 10 and its HDR10 fixture shared one tuple, and both failed.

R22 receipts store RMS and calibration values, not raw PCM windows. The first-sample-excluded RMS cannot be reconstructed from them. No completed R22 Dolby layout needed replay for an admission decision: each already had an unchanged measured failure. The table reports actual R22/R23 scores, not synthetic reclassifications. **P/F/U** is positions passed / measured failure / unmeasured. For Dolby, loss and A/V are worst observed milliseconds; margin is the worst measured transient margin in thousandths of full-scale RMS (negative fails). These extrema describe measured takes only, not an all-take bound; the receipts include coverage per metric.

| Layout | 10.11.11 R22 P/F/U | Loss / A/V / margin | 12.4.0 rule and P/F/U | Loss / A/V / margin |
|---|---:|---:|---:|---:|
| `mkv-h264-ac351` | 8/14/2 | 0.000 / 0.000 / −4.456 | R22 8/16/0 | 39.000 / 0.002 / −4.568 |
| `mkv-h264-eac351` | 5/17/2 | 0.021 / 0.021 / −4.475 | R22 5/19/0 | 39.000 / 0.023 / −4.475 |
| `mkv-hevc10-eac351` | 5/17/2 | 0.021 / 0.021 / −4.475 | R23 5/19/0 | 39.000 / 0.023 / −4.475 |
| `mkv-hevc10-hdr10-eac351` | 5/17/2 | 0.021 / 0.021 / −4.475 | R23 5/19/0 | 39.000 / 0.023 / −4.475 |
| `mp4-h264-eac351` | 9/15/0 | 38.979 / 0.031 / −4.568 | R23 9/15/0 | 38.979 / 0.031 / −4.475 |
| `mkv-h264-eac351+aac-srt` | 6/16/2 | 0.021 / 0.021 / −4.475 | R23 6/18/0 | 39.000 / 0.023 / −4.475 |
| `mkv-h264-eac351-ass+srt` | 6/16/2 | 0.021 / 0.021 / −2.986 | R23 6/18/0 | 39.000 / 0.023 / −4.005 |
| `mp4-h264-eac351-mov_text` | 9/15/0 | 38.979 / 0.031 / −4.568 | R23 9/15/0 | 38.979 / 0.031 / −4.475 |
| `mkv-h264-flac` | 15/8/1 | — | Not run; refused on other grounds | — |
| `mp4-h264-aac` | 2/22/0 | — | Not run; refused on other grounds | — |
| `mkv-h264-aac` | 0/22/2 | — | Not run; refused on other grounds | — |
| `mkv-hevc10-aac` | 0/22/2 | — | Not run; refused on other grounds | — |
| `mp4-hevc10-aac` | 2/22/0 | — | Not run; refused on other grounds | — |
| `mp4-h264-aac-hard-original` | 7/17/0 | — | Not run; refused on other grounds | — |

- **Why Dolby stays off:** the six R23 layouts still had onset-only failures at −1 ms (47 takes; worst margin −0.004475 full-scale RMS) and +520 ms (54 takes; worst −0.000724); no onset-only failure occurred at −200 ms. These targets are outside the exception. The receipts also contain failures on unchanged checks, including unproven sound identity and splice excess. A negative margin is a measurement failure, not a finding that a person heard a pop.
- **FLAC and AAC:** FLAC lost 65 ms of known kept sound at `[3039, 7999) ms` in straight playback and still awaits a fix or owner acceptance. All five AAC layouts presented marked sound on 10.11.11; the two Matroska layouts also presented marked pictures. FLAC and AAC were deliberately not run on 12.4.0 after the timeout, because neither is eligible for admission. There is no measured 12.4.0 result for them.
- **Supporting checks:** plugin 6/6 on each pinned server (one 10.11 shelf-isolation retry), Delivery 202/202 and Marking 43/43 on each SDK, PWA 9/9, and Python oracle 31/31. No renderer or register change.

Full per-take JSON receipts (gzip): [10.11.11 R22](../../../tools/playback-lab/prototypes/common-formats/receipt-r22-10.11-2026-11-25.json.gz), [12.4.0 partial R22](../../../tools/playback-lab/prototypes/common-formats/receipt-r22-12.4-partial-2026-11-25.json.gz), [12.4.0 R23 Dolby recovery](../../../tools/playback-lab/prototypes/common-formats/receipt-r23-12.4-dolby-2026-11-25.json.gz). R21 results below are historical, not R22 or R23 evidence.

## Historical R21 admission standard

[R21](../../../.claude/41-QUESTIONS-RESOLVED.md) requires **zero marked picture and zero marked sound**, with exact kept picture and sound at straight cuts and exact seek landings. Only a fresh start or resume near a join may play one extra **kept** sample before the start point (about 21 µs at 48 kHz), compared with the uncut original starting on the same picture. Missing, altered or shifted kept samples still refuse; R20's sample-for-sample check on AAC is unchanged. “Experimental” here only means a measured desktop-mpv lab offer; it is not household, TV or Wholphin support.

`tools/playback-lab/format-matrix.py` measures each layout through the plugin's real PlaybackInfo, ticket and cut-list path, then republishes the reviewed mark at **24 cut positions** (`POSITIONS`): on and off frame instants and AC-3/E-AC-3 packet edges (MP4 every 32 ms; Matroska 32 ms minus the 5.333 ms priming), 1 ms before and after a frame instant, a one-frame mark, two 1 ms marks (one with no frame inside it, sound only), both edges on keyframes, marks from the very start and to the very end of the file. At each position it plays:

- the straight cut: every kept frame once and in order, no marked frame, and every sample of every channel equal to mpv's uncut decode with exactly the marked samples removed;
- fresh starts 200 ms and 1 ms before the join, on it, and 520 ms after it;
- paused exact seeks onto the join, 1.3 ms and 41.3 ms before it, forward across it and back across it; one in-session resume after the final near-join seek with frame-complete picture and joint PCM to end of file;
- for subtitle layouts, per-track controls at the fixture's own mark; at each position the default-selected track must hide any cue touching the mark and keep every untouched cue.

A layout is admitted only if every position passes on that line. The run continues after failures; a timeout is unmeasured, never a pass. A partial sweep (`--positions N` or `--stop-on-fail`) never admits. The extra-prefix test compares bit-exact interleaved PCM on the source clock; it checks the filtered stream's preceding kept indices against the marked-side seam candidates, and independently measures the uncut start. An original prefix sample inside the mark is not a kept sample. An unidentified or ambiguous sample always refuses.

The old fixture shared periodic 440 Hz audio across channels and could alias the preceding kept and marked samples for a whole-period mark. The R21 fixture keeps the channel-zero tone as an independent clock check and adds a deterministic aperiodic pilot to the other channels. Its source fingerprints differ from the old run; the legacy hard-fixture AAC source is swept unchanged as a separate diagnostic. The oracle does not guess sample identity from the waveform's frequency or realign PCM to make a mismatch pass.

**The oracle now uses the true source clock.** The old oracle assumed mpv's uncut decode starts at source sample 0. For Matroska AC-3/E-AC-3 it does not: mpv plays one priming sample first, because ffmpeg stores Matroska `CodecDelay` truncated to whole nanoseconds (256/48000 s becomes 5333333 ns), so mpv's start computation lands on 255.99998 samples and truncates to 255. Proof: mpv's uncut output sample *i* is bit-identical to ffmpeg's decode (with priming) sample *i* + 255, and the fixture tone's phase puts it exactly one sample early. Because the old oracle used mpv's own uncut decode as the truth, the "exact" Matroska rows from PR #19 hid one wrong sample per join: their boundaries 5 µs *before* the instant passed only against that one-sample-early reference, which on the true clock means the last kept sample before the join was dropped and the last marked sample played (the measured boundary windows: on the old reference −20…0 µs passed and +1…+15 µs failed; on the true clock it is the reverse). The new oracle locates the uncut decode's lead from the tone's phase (the tone is a function of the integer sample index) and places every mark on the true clock.

## The renderer fix (adapter revisions 4 and 5)

From mpv 0.40's source: a picture survives a segment iff its time is at or after the segment start and before its end (`filters/f_decoder_wrapper.c`); a sound frame keeps or skips `(int)((boundary − frame time) × rate)` samples (`audio/aframe.c`, `mp_aframe_clip_timestamps`). So a sample survives a segment end iff its end instant is at or before the boundary. Whole-millisecond cut times sit exactly on 48 kHz sample instants, so the input to that truncation is an integer up to floating-point error: that is the tie.

The new **split** form, used for AC-3, E-AC-3 and FLAC sound: picture and sound in separate partitions of the same source; at every mark edge the picture boundary sits 2 µs before the frame instant and the sound boundary 10 µs (half a sample) after the sample instant. The presentation's own start and the end of the file stay where the original's are, so after every join sound and picture keep the original's relative position. The kept frames and samples are exactly those whose instants are outside the marks. The plugin passes the playing track's sample rate; the renderer refuses rates whose milliseconds are not samples (e.g. 44.1 kHz) and picture clocks with a tick inside the 2 µs lead. Subtitle partitions follow the picture clock. The adapter revision names the form (2/3 joint, 4/5 split), so old evidence can never admit the new form. AAC and Opus stay on the joint form: in a sound-only MP4 partition they lose their decoder history.

<a id="per-layout"></a>

## R21 results

**0 of 14 layouts admitted under R21 on both lines.** Jellyfin 10.11.11: 0/14. Jellyfin 12.4.0: 0/14. Any admitted row is `experimental` desktop mpv only and discloses the one-kept-sample limit. The original MP4 AAC fixture is a separate diagnostic, not a candidate row. Both historical rows (Wholphin revision 1 and `lab-hard-fixture-v0`) stay refused; the desktop sweep does not qualify Wholphin.

Both MP4 E-AC-3 layouts passed 18/24 on each line. Kept samples were lost at several starts, and one head cut lost kept PCM even in straight playback. These are not the one extra kept sample R21 allows. The unchanged MP4 AAC hard fixture passed 8/24 on each line and presented a marked sample. Neither historical AAC row remains experimental.

Counts are positions that passed out of the 24 planned. A layout continues past a failed position. Opus is an exception to position-level playback: the uncut control could not establish a whole-sample source clock, so all 24 positions per line are unmeasured. The two 10.11 head positions listed as unmeasured for some Matroska layouts were attempted but produced no usable paired capture. The receipts distinguish tolerated extra kept samples from rejected differences. A pass at every position is necessary; the separate original-mark controls and every fixture sharing the exact tuple must also pass.

| Layout | 10.11 passed/24 | 12.4 passed/24 | Deviations (10.11; 12.4 if different) |
|---|---:|---:|---|
| `mp4-h264-aac` | 2/24 | 2/24 | audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost, kept-sample-shifted, marked-sample-presented |
| `mkv-h264-aac` | 0/24 | 0/24 | audio-identity-unproven, kept-picture-missing-altered-or-shifted, kept-sample-altered, kept-sample-lost, marked-frame-presented |
| `mkv-h264-ac351` | 18/24 (2 unmeasured) | 18/24 | 10.11: audio-identity-unproven, extra-kept-sample, kept-sample-altered, unmeasured; 12.4: audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost |
| `mkv-h264-eac351` | 18/24 (2 unmeasured) | 18/24 | 10.11: audio-identity-unproven, extra-kept-sample, kept-sample-altered, unmeasured; 12.4: audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost |
| `mkv-hevc10-aac` | 0/24 | 0/24 | audio-identity-unproven, kept-picture-missing-altered-or-shifted, kept-sample-altered, kept-sample-lost, marked-frame-presented |
| `mkv-hevc10-eac351` | 18/24 (2 unmeasured) | 18/24 | 10.11: audio-identity-unproven, extra-kept-sample, kept-sample-altered, unmeasured; 12.4: audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost |
| `mkv-hevc10-hdr10-eac351` | 18/24 (2 unmeasured) | 18/24 | 10.11: audio-identity-unproven, extra-kept-sample, kept-sample-altered, unmeasured; 12.4: audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost |
| `mp4-hevc10-aac` | 2/24 | 2/24 | audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost, kept-sample-shifted, marked-sample-presented |
| `mp4-h264-eac351` | 18/24 | 18/24 | audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost |
| `mkv-h264-flac` | 10/24 | 10/24 | extra-kept-sample, kept-sample-altered, kept-sample-lost |
| `mkv-h264-opus` | 0/24 (24 unmeasured) | 0/24 (24 unmeasured) | unmeasured |
| `mkv-h264-eac351+aac-srt` | 17/24 (2 unmeasured) | 17/24 | 10.11: audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost, unmeasured; 12.4: audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost |
| `mkv-h264-eac351-ass+srt` | 16/24 (2 unmeasured) | 16/24 | 10.11: audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost, subtitle-deviation, unmeasured; 12.4: audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost, subtitle-deviation |
| `mp4-h264-eac351-mov_text` | 18/24 | 18/24 | audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost |
| `mp4-h264-aac-hard-original` | 8/24 | 8/24 | audio-identity-ambiguous, audio-identity-unproven, extra-kept-sample, kept-sample-altered, kept-sample-lost, kept-sample-shifted, marked-sample-presented |

Receipts: [10.11.11](../../../tools/playback-lab/prototypes/common-formats/receipt-r21-10.11-2026-11-25.json), [12.4.0](../../../tools/playback-lab/prototypes/common-formats/receipt-r21-12.4-2026-11-25.json). The 12.4 receipt combines completed layouts from three isolated lab runs after two watchdog stops. The merge checked the exact plugin and player builds and settings, and kept the input fingerprint of the run that actually measured each layout.

## Historical strict sweep (PR #22, before R21)

The original strict sweep stopped a layout at its first failing position (one failure refused it). Results were the same on Jellyfin 10.11.11 and 12.4.0 for every layout. "Positions exact" counts the positions in `POSITIONS` order before the first failure; 24 are needed.

| Layout | Positions exact (each line) | First failure | Why |
|---|---|---|---|
| MP4 · H.264 · E-AC-3 5.1 | 4, then fail | `[4000, 4001) ms` | Fresh starts 1 ms before and on the join play one extra kept sample; the original at that picture plays none. Cuts and seeks exact. |
| MP4 · H.264 · E-AC-3 5.1 + `mov_text` | 4, then fail | `[4000, 4001) ms` | As above. Subtitle checks pass (no touching text shown, kept cues shown). |
| MKV · H.264 · AC-3 5.1 | 7, then fail | `[3039, 7999) ms` | 47 samples wrong after the join: the sound decoder starts at the join's frame without the previous frame's overlap (mpv's Matroska seek pre-roll cancels). Also extra kept samples at fresh starts. |
| MKV · H.264 · E-AC-3 5.1 | 7, then fail | `[3039, 7999) ms` | As above (48 samples). |
| MKV · HEVC Main 10 · E-AC-3 5.1 | 7, then fail | `[3039, 7999) ms` | As above. |
| MKV · HEVC Main 10 + HDR10 metadata · E-AC-3 5.1 | 7, then fail | `[3039, 7999) ms` | As above. |
| MKV · H.264 · E-AC-3 5.1 + AAC + default SubRip | 7, then fail | `[3039, 7999) ms` | As above; subtitle checks pass. |
| MKV · H.264 · E-AC-3 5.1 + forced ASS + SubRip | 0 | fixture mark | 128 samples missing right after the join on 10.11 (the plan duration of this file is also 5 ms shorter); 384 samples wrong after the join on 12.4. Not explained; refused. Subtitle leak check holds. |
| MKV · H.264 · FLAC stereo | 3, then fail | `[2000, 2040) ms` | Fresh start on the join plays one extra kept sample; the original does not. |
| MP4/MKV · AAC (4 layouts) | 0 | fixture mark | PNS noise, rounding, Matroska timing (PR #21). |
| MKV · H.264 · Opus | unmeasured | — | The uncut decode is not on a whole-sample clock relative to the source. |

A diagnostic run of MP4 E-AC-3 on plain files (`--local --keep-going`, the same cut-list form) measured all 24 positions: the straight cut was exact at 23 and every seek at all 24, so 14 positions passed outright. Nine failed only because fresh starts near the join played one (once two) extra **kept** samples where the original plays fewer, and the tenth (the cut from the very start to 1 ms after a frame) played one extra kept sample before its first picture. No marked sample played anywhere by mechanism (the split form removes marked sound before mpv's start clipping). One take could not be told apart by sample identity, because that mark is a whole number of 440 Hz periods long; it counts as marked.

Receipts: [`receipt-sweep-10.11-2026-11-25.json`](../../../tools/playback-lab/prototypes/common-formats/receipt-sweep-10.11-2026-11-25.json), [`receipt-sweep-12.4-2026-11-25.json`](../../../tools/playback-lab/prototypes/common-formats/receipt-sweep-12.4-2026-11-25.json). Register: 25 new `refused` rows (adapter revisions 2, 4 and 5; on 12.4 the HEVC E-AC-3 file with HDR10 mastering metadata gets the same label as the one without, so they share a row); no `experimental` common-format row remains. The two hard-fixture rows (MP4 AAC, Wholphin revision 1 and `lab-hard-fixture-v0`) were not part of that historical run.

## What still blocks, and why a cut list cannot fix it

1. **Fresh starts (all layouts).** After a seek, mpv starts sound at the first shown picture with the same truncation, and whether it lands one sample early depends on how the decoder got there, not on the cut list. Measured on a plain one-range cut list of MP4 E-AC-3: starting at frames 75, 132 and 175 (mid-GOP) plays one sample from before the start unless sound is placed more than one sample early; starting at frames 200 and 301 (at or just after a keyframe) is exact only if sound is placed less than one sample early. The two windows do not overlap, so no static placement is exact at both. In the filtered timeline the extra sample is the filtered stream's own preceding sample, which the split form makes a kept one (the marked samples were already removed by the segment boundary); the unfiltered original at the same picture plays none, because its decoder took a different path.
2. **Matroska AC-3/E-AC-3 joins.** mpv's Matroska seek gives the sound decoder no history: it skips blocks whose stored time is before the target minus the codec delay, and the stored times already include the codec delay, so the pre-roll cancels. When a mark ends inside the first 256 samples of an AC-3 frame, the first 256 samples of that frame decode without the previous frame's overlap: 48 samples after the join were wrong at the mark `[3039, 7999) ms`. Separately, sound decoded from the file's very first block sits one sample later than sound decoded after any other seek.
3. **AAC** (PR #21): noise substitution, the same truncation, and Matroska's millisecond timing. **Opus**: the uncut decode is not on a whole-sample clock relative to the source (fractional tone phase), so it is unmeasured.

## What an upstream mpv change would remove (draft for a human to file; not filed)

> **Round, don't truncate, when clipping audio frames to segment and start boundaries.** `mp_aframe_clip_timestamps()` in `audio/aframe.c` computes the samples to keep or skip as `(int)((end - f->pts) * rate)` and `(int)((start - f->pts) * rate)`. When a boundary falls exactly on a sample instant (common: EDL and `--start` times in whole milliseconds at 48 kHz), floating-point error decides whether one sample is dropped or kept, so the same EDL can lose a sample or play one from the wrong side of a join depending on the position. The same function clips the first frame after a seek against the video start time, so a fresh start can play a sample from before the start position. Proposal: use `lrint()` (round to nearest), so a boundary on a sample instant is decided by that instant, matching how video frames are kept (`pts >= start`). A Matroska `CodecDelay` stored truncated to whole nanoseconds (e.g. 5333333 ns for 256 samples at 48 kHz) then also stops adding one priming sample at the start of playback. Separately, in `demux/demux_mkv.c` the seek pre-roll (`seek_preroll = codec_delay`) is cancelled because blocks are skipped by their stored time, which already includes the codec delay; AC-3/E-AC-3 then decode the first frame after a seek without the previous frame's overlap.

With rounding to nearest, a cut list could put sound boundaries exactly on the instant again; the split partitions and their offsets would be unnecessary, and MP4 AAC could keep the joint partition's pre-roll.

## What this proves and what it does not

- **Scope:** desktop mpv 0.40 in the lab container, decoded PCM, through the plugin on pinned Jellyfin 10.11.11 and 12.4.0. Evidence belongs to the exact layout and server/plugin/player tuple, not the codec family.
- **Does not prove:** any TV app, Wholphin audio, HDMI output, passthrough, persisted resume/restart, real encoder settings, other frame rates or sample rates. Paused seeks present no audio; the resumed take separately measures PCM from the final near-join landing onward.
