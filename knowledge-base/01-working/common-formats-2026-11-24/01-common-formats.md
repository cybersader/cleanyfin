# Common movie formats: which stream layouts play filtered exactly

**2026-11-24 · lab research and plugin change, desktop mpv only · branch `feat/common-formats`.** Owner decision (question dialog, 2026-11-24): *"Your real movies can now be marked and approved, but only the lab clip's format plays filtered. What should I do next?"* → *"Measure common movie formats (Recommended) — Make lab copies in the most common formats (MKV with H.264 or HEVC video, and AAC, AC3 or EAC3 sound) and prove filtered playback is exact on each, so typical movies can play filtered."*

## Answer

**5 of 12 common layouts now play filtered exactly** (desktop mpv, decoded PCM, through the plugin on both Jellyfin 10.11.11 and 12.4.0, same result on both). They are the ones with Dolby sound: AC-3 5.1 and E-AC-3 5.1 in MKV (H.264 or HEVC Main 10) and E-AC-3 5.1 in MP4. They need the static player setting `cons_noisegen=1`. Every layout with AAC, FLAC or Opus sound, and the file with a default subtitle track, is refused, each for a measured reason. Getting there needed three real fixes: MKV cut lists were written on the wrong clock, Matroska frame boundaries tie, and MP4 E-AC-3 files could not be bound at all.

"Exact" is the same bar as the [audio-at-the-join research](../exact-audio-2026-11-22/01-audio-at-the-join.md): with picture and sound from one playback, every kept frame appears once and in order, no marked frame appears, and every kept sample on every channel equals mpv's uninterrupted decode of the same file with exactly the marked samples removed. No alignment, no tolerance. This must also hold for fresh starts before, at and after the join and for seeks across and onto it. Anything less is stated, and that layout gets no offer.

## Per layout

Synthetic copies of the hard fixture only (barcoded 1280×720 25 fps pictures, GOP 50 with B-frames, one cut of source frames 137–262 inside a GOP and inside an audio packet; every channel a different level of the 440/880/660 Hz tone). Each layout was measured through the plugin's real path: the household verifier hashes and probes the file, a reviewed mark is published for it, the plugin answers PlaybackInfo with a ticketed cut list, and mpv plays it in the server's private loopback namespace. This was done on both Jellyfin lines. Reproduce with `python3 tools/playback-lab/format-matrix.py`.

| Layout | 10.11.11 | 12.4.0 | What decides it |
|---|---|---|---|
| MKV · H.264 · AC-3 5.1 | **exact** | **exact** | Every kept frame and sample on all 6 channels; starts before/at/after the join and every seek exact. Boundaries 5 µs before the instant; `cons_noisegen=1`. |
| MKV · H.264 · E-AC-3 5.1 | **exact** | **exact** | As above. |
| MKV · HEVC Main 10 · E-AC-3 5.1 | **exact** | **exact** | As above; the video codec made no difference. |
| MKV · HEVC Main 10 + HDR10 mastering metadata · E-AC-3 5.1 | **exact** | **exact** | The fixture's transfer stayed untagged (not PQ), so Jellyfin catalogues it as SDR and it has the same tuple as the row above. **No HDR10 claim**: a file Jellyfin sees as HDR10 gets a different label and is refused until measured. |
| MP4 · H.264 · E-AC-3 5.1 | **exact** | **exact** | Exact after the probe fix. A fresh start emits one kept sample from before the start position, and the unfiltered original does the same (player start behaviour, not filtering). |
| MP4 · H.264 · AAC-LC stereo | refused | refused | Up to 3 LSB on one channel after the join (AAC noise substitution); a start after the join adds one sample the original does not. |
| MP4 · HEVC Main 10 · AAC-LC stereo | refused | refused | Same as above. |
| MKV · H.264 · AAC-LC stereo | refused | refused | Millisecond timestamps cannot place AAC frames: sound 15–17 samples off throughout; one marked frame at the join (joint form). |
| MKV · HEVC Main 10 · AAC-LC stereo | refused | refused | Same as above. |
| MKV · H.264 · FLAC stereo | refused | refused | Straight cut and seeks exact (split partitions); a start exactly at the join emits the last **marked** sample. |
| MKV · H.264 · Opus stereo | refused | refused | 48 samples before the join differ; one marked frame at the join. |
| MKV · H.264 · E-AC-3 5.1 default + AAC stereo + default SubRip | refused | refused | Picture and sound exact; mpv plays the default E-AC-3 track, which the plugin projects. Refused because a subtitle line that **starts inside the mark** is shown after the join. |

Diagnostic, not a layout: the same MP4 AAC file encoded without noise substitution has an exact straight cut, which confirms the cause. The hard fixture's MP4 AAC (no noise substitution) keeps its existing rows. Receipts: [`receipt-10.11-2026-11-24.json`](../../../tools/playback-lab/prototypes/common-formats/receipt-10.11-2026-11-24.json), [`receipt-12.4-2026-11-24.json`](../../../tools/playback-lab/prototypes/common-formats/receipt-12.4-2026-11-24.json). Register rows: [`register-v1.json`](../../../delivery/evidence/register-v1.json) (exact → `experimental`, lab flag only; refused → `refused`).

**Same file, two clocks.** Jellyfin 10.11 (ffprobe 7.1.4) reads MKV sound priming as a negative first timestamp (−5 ms AC-3, −21 ms AAC); Jellyfin 12.4 (ffprobe 8.1.3) reads it as 0. Both measured exact, because the cut list follows the clock the server verified. But the same MKV bytes are a different edition on each line: after upgrading Jellyfin, marked MKV titles will show "file changed, review again". See the upkeep ledger.

## What was wrong, and what changed

| Finding | Effect before | Change |
|---|---|---|
| mpv reads cut-list times as the **file's own timestamps**. The plan's source zero is the earliest stream start, and in Matroska that is before file time zero (encoder priming: AAC −21 ms, AC-3/E-AC-3 −5 ms, Opus −7 ms). | Every MKV cut landed late by that offset: one marked frame shown, one kept frame lost, sound misplaced. MP4 hid this because it starts at 0. | The renderer (adapter revision 2) adds the file time of source zero, derived exactly from the verified track clocks. If the tracks disagree, or zero is not a whole microsecond, it refuses (`UnverifiedPlayerClock`). |
| In Matroska, a boundary **exactly on a frame instant** keeps that frame (mpv reads Matroska frame times a hair early). | One marked frame at the join; a seek to just before the join landed on a marked frame. | For Matroska with AC-3/E-AC-3, interior boundaries are written 5 µs (a quarter sample) before the instant. Measured: the same kept frames and the same kept samples. The same shift *loses* a sample with AAC and FLAC, so it is not used there. |
| A shared boundary cannot be both "just before" for picture and "just after" for sound. | — | The **split-partition** form (picture and sound in separate partitions of the same source) is implemented and measured. FLAC in MKV is exact on the straight cut and seeks with it. AAC and AC-3 lose their decoder history in a sound-only partition, so they keep the joint form. FLAC is still refused, for a different reason (below). |
| AC-3/E-AC-3 decoders add dither from a random state. | Kept sound differed by 1 LSB after the join. | Measured with the static player setting `ad-lavc-o=cons_noisegen=1` (named in the evidence tuple's device mode). A player without it is a different tuple. |
| The plugin hard-coded the lab clip's layout (1280×720, 25 fps, stereo AAC-LC, MP4, track indexes 0/1). | Every other file: `EvidenceTupleUnknown` before the register was even read. | `EvidenceSourceLabel` derives the layout from the verified file. It records container family, every track's codec, profile, bit depth, range, geometry, rate, channels, time base and clock anchor, which sound track plays, and the subtitles. The register then decides. A layout the label cannot state (no or ambiguous default sound, cover art or attachments, unknown facts) is refused. PlaybackInfo, the profile check and the projected source follow the measured layout: container family, codecs and the default sound track index. |

## Refused, and why

- **MP4/MKV + AAC.** AAC with perceptual noise substitution (PNS, which ffmpeg's encoder uses by default) fills noise bands from a decoder random generator, and after a join its state differs: up to 3 LSB on one channel, never marked material. The same file encoded without PNS measured exact on the straight cut. In Matroska, millisecond timestamps cannot place AAC's 21.333 ms frames: the plain and cut-list paths disagree by 15–17 samples. The [hard fixture's AAC](../exact-audio-2026-11-22/01-audio-at-the-join.md) (no PNS, identical channels) stays exact as before.
- **MKV + FLAC.** The straight cut and seeks are exact with split partitions, but a fresh start *exactly at the join* emits one sample from before the start position, and that sample is the last marked one (source sample 504959). One marked sample is a leak.
- **MKV + Opus.** 48 samples before the join differ (Opus pre-roll/overlap).
- **Subtitles.** A subtitle line that lies wholly inside a mark never appears. A line that *starts inside* a mark and ends after it is shown after the join with its full text. The picture and sound are exact, but the line's words may belong to the marked content. That is a policy question, not a timing fault (see the owner decision in the report).

## What this proves and what it does not

- **Proves:** desktop mpv 0.40 in the lab container, decoding to PCM, plays these synthetic layouts exactly through the plugin's real PlaybackInfo, ticket and cut-list path on Jellyfin 10.11.11 and 12.4.0. This covers one cut position per layout and the measured starts and seeks.
- **Does not prove:** any TV app (Wholphin or otherwise), Android's audio path, HDMI output, a real movie's encoder settings, other cut positions, persisted resume or restart. **No TV or Wholphin tuple is promoted from this evidence.** The rows are `experimental` (lab only), for the exact tuple `mpv 0.40.0` + image digest + device mode + plugin build + Jellyfin version.
- **Passthrough.** AC-3/E-AC-3 bitstreamed undecoded to a receiver cannot be cut to the sample by any player; at best whole coded frames (32 ms) are dropped and the receiver decodes the rest. The design therefore treats passthrough as a **different output mode that is refused**. Evidence rows exist only for decoded-PCM device modes, a passthrough mode never matches them (contract test `DecodedPcmEvidenceNeverAdmitsPassthroughOrAnotherDevice`), and a TV measurement must verify decoded PCM arrived (the HDMI harness already checks for bitstream on every take). A future frame-granular passthrough adapter would need its own action semantics, not a relabelled cut.
