# Exact at every cut position, or refused: the boundary sweep

**2026-11-25 · lab research, renderer change, desktop mpv only · branch `feat/boundary-sweep` (PR #20 + PR #21).** Follows the AAC finding that mpv truncates the sound boundary of a cut-list segment to a whole sample, and that MP4 E-AC-3, admitted "exact" from one cut position, was wrong at others.

## Answer

**No layout is exact at every cut position on either server line, so every common-format and subtitle layout stays refused.** The joins themselves are now fixed for MP4 E-AC-3: with the new cut-list form, the straight cut and the paused seeks are exact at all 24 measured positions. What still fails is the fresh start. When playback starts near a join, mpv sometimes plays one extra sound sample from just before the start position. That sample is always a kept one, never a marked one. The unfiltered original, started at the same picture, plays none, so under the project's rule (R20: never admit an inexact layout) the layout stays refused. Matroska AC-3/E-AC-3 additionally fails at joins, for a reason no cut list can fix (below).

Whether one extra *kept* sample at a fresh start counts as exact is an owner decision; see [the question](#owner-question).

## The admission standard (what a row must now pass)

`tools/playback-lab/format-matrix.py` measures each layout through the plugin's real PlaybackInfo, ticket and cut-list path, then republishes the reviewed mark at **24 cut positions** (`POSITIONS`): on and off frame instants and AC-3/E-AC-3 packet edges (MP4 every 32 ms; Matroska 32 ms minus the 5.333 ms priming), 1 ms before and after a frame instant, a one-frame mark, two 1 ms marks (one with no frame inside it, sound only), both edges on keyframes, marks from the very start and to the very end of the file. At each position it plays:

- the straight cut: every kept frame once and in order, no marked frame, and every sample of every channel equal to mpv's uncut decode with exactly the marked samples removed;
- fresh starts 200 ms and 1 ms before the join, on it, and 520 ms after it;
- paused exact seeks onto the join, 1.3 ms and 41.3 ms before it, forward across it and back across it;
- for subtitle layouts, the existing per-track checks at the fixture's own mark, plus at every position: no cue touching the mark is shown, every other cue is.

A layout is admitted only if every position passes on that line. A partial sweep (`--positions N`) never admits.

**The oracle now uses the true source clock.** The old oracle assumed mpv's uncut decode starts at source sample 0. For Matroska AC-3/E-AC-3 it does not: mpv plays one priming sample first, because ffmpeg stores Matroska `CodecDelay` truncated to whole nanoseconds (256/48000 s becomes 5333333 ns), so mpv's start computation lands on 255.99998 samples and truncates to 255. Proof: mpv's uncut output sample *i* is bit-identical to ffmpeg's decode (with priming) sample *i* + 255, and the fixture tone's phase puts it exactly one sample early. Because the old oracle used mpv's own uncut decode as the truth, the "exact" Matroska rows from PR #19 hid one wrong sample per join: their boundaries 5 µs *before* the instant passed only against that one-sample-early reference, which on the true clock means the last kept sample before the join was dropped and the last marked sample played (the measured boundary windows: on the old reference −20…0 µs passed and +1…+15 µs failed; on the true clock it is the reverse). The new oracle locates the uncut decode's lead from the tone's phase (the tone is a function of the integer sample index) and places every mark on the true clock.

## The renderer fix (adapter revisions 4 and 5)

From mpv 0.40's source: a picture survives a segment iff its time is at or after the segment start and before its end (`filters/f_decoder_wrapper.c`); a sound frame keeps or skips `(int)((boundary − frame time) × rate)` samples (`audio/aframe.c`, `mp_aframe_clip_timestamps`). So a sample survives a segment end iff its end instant is at or before the boundary. Whole-millisecond cut times sit exactly on 48 kHz sample instants, so the input to that truncation is an integer up to floating-point error: that is the tie.

The new **split** form, used for AC-3, E-AC-3 and FLAC sound: picture and sound in separate partitions of the same source; at every mark edge the picture boundary sits 2 µs before the frame instant and the sound boundary 10 µs (half a sample) after the sample instant. The presentation's own start and the end of the file stay where the original's are, so after every join sound and picture keep the original's relative position. The kept frames and samples are exactly those whose instants are outside the marks. The plugin passes the playing track's sample rate; the renderer refuses rates whose milliseconds are not samples (e.g. 44.1 kHz) and picture clocks with a tick inside the 2 µs lead. Subtitle partitions follow the picture clock. The adapter revision names the form (2/3 joint, 4/5 split), so old evidence can never admit the new form. AAC and Opus stay on the joint form: in a sound-only MP4 partition they lose their decoder history.

## Measured results (both server lines through the plugin)

<a id="per-layout"></a>The sweep stops a layout at its first failing position (one failure refuses it). Results were the same on Jellyfin 10.11.11 and 12.4.0 for every layout. "Positions exact" counts the positions in `POSITIONS` order before the first failure; 24 are needed.

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

Receipts: [`receipt-sweep-10.11-2026-11-25.json`](../../../tools/playback-lab/prototypes/common-formats/receipt-sweep-10.11-2026-11-25.json), [`receipt-sweep-12.4-2026-11-25.json`](../../../tools/playback-lab/prototypes/common-formats/receipt-sweep-12.4-2026-11-25.json). Register: 25 new `refused` rows (adapter revisions 2, 4 and 5; on 12.4 the HEVC E-AC-3 file with HDR10 mastering metadata gets the same label as the one without, so they share a row); no `experimental` common-format row remains. The two hard-fixture rows (MP4 AAC, Wholphin revision 1 and lab revision `lab-hard-fixture-v0`) were not part of this sweep and are unchanged; they too rest on one cut position.

## What still blocks, and why a cut list cannot fix it

1. **Fresh starts (all layouts).** After a seek, mpv starts sound at the first shown picture with the same truncation, and whether it lands one sample early depends on how the decoder got there, not on the cut list. Measured on a plain one-range cut list of MP4 E-AC-3: starting at frames 75, 132 and 175 (mid-GOP) plays one sample from before the start unless sound is placed more than one sample early; starting at frames 200 and 301 (at or just after a keyframe) is exact only if sound is placed less than one sample early. The two windows do not overlap, so no static placement is exact at both. In the filtered timeline the extra sample is the filtered stream's own preceding sample, which the split form makes a kept one (the marked samples were already removed by the segment boundary); the unfiltered original at the same picture plays none, because its decoder took a different path.
2. **Matroska AC-3/E-AC-3 joins.** mpv's Matroska seek gives the sound decoder no history: it skips blocks whose stored time is before the target minus the codec delay, and the stored times already include the codec delay, so the pre-roll cancels. When a mark ends inside the first 256 samples of an AC-3 frame, the first 256 samples of that frame decode without the previous frame's overlap: 48 samples after the join were wrong at the mark `[3039, 7999) ms`. Separately, sound decoded from the file's very first block sits one sample later than sound decoded after any other seek.
3. **AAC** (PR #21): noise substitution, the same truncation, and Matroska's millisecond timing. **Opus**: the uncut decode is not on a whole-sample clock relative to the source (fractional tone phase), so it is unmeasured.

## What an upstream mpv change would remove (draft for a human to file; not filed)

> **Round, don't truncate, when clipping audio frames to segment and start boundaries.** `mp_aframe_clip_timestamps()` in `audio/aframe.c` computes the samples to keep or skip as `(int)((end - f->pts) * rate)` and `(int)((start - f->pts) * rate)`. When a boundary falls exactly on a sample instant (common: EDL and `--start` times in whole milliseconds at 48 kHz), floating-point error decides whether one sample is dropped or kept, so the same EDL can lose a sample or play one from the wrong side of a join depending on the position. The same function clips the first frame after a seek against the video start time, so a fresh start can play a sample from before the start position. Proposal: use `lrint()` (round to nearest), so a boundary on a sample instant is decided by that instant, matching how video frames are kept (`pts >= start`). A Matroska `CodecDelay` stored truncated to whole nanoseconds (e.g. 5333333 ns for 256 samples at 48 kHz) then also stops adding one priming sample at the start of playback. Separately, in `demux/demux_mkv.c` the seek pre-roll (`seek_preroll = codec_delay`) is cancelled because blocks are skipped by their stored time, which already includes the codec delay; AC-3/E-AC-3 then decode the first frame after a seek without the previous frame's overlap.

With rounding to nearest, a cut list could put sound boundaries exactly on the instant again; the split partitions and their offsets would be unnecessary, and MP4 AAC could keep the joint partition's pre-roll.

## Owner question

*"With the new cut list, MP4 E-AC-3 cuts and seeks are exact at all 24 tested positions. Starting playback near a cut sometimes plays one extra kept sound sample (21 µs) from just before the start point; never marked sound. The unfiltered movie, started at the same picture, plays none. Should that count as exact?"* Until answered, it does not, and every layout stays refused.

## What this proves and what it does not

- **Proves:** desktop mpv 0.40 in the lab container, decoded PCM, through the plugin on Jellyfin 10.11.11 and 12.4.0: which positions of the sweep are exact per layout (receipts below).
- **Does not prove:** any TV app, HDMI output, passthrough, real encoder settings, other frame rates or sample rates. No TV or Wholphin tuple is promoted.
