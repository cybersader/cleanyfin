# Exact audio at a cut: experiment scripts

These scripts compare what real mpv outputs for a cut list against the ideal: decode the uninterrupted original once, then delete exactly the marked samples. The comparison is sample by sample on both channels with no alignment adjustment. Findings are written up in [`knowledge-base/01-working/exact-audio-2026-11-22/01-audio-at-the-join.md`](../../../../knowledge-base/01-working/exact-audio-2026-11-22/01-audio-at-the-join.md).

They need Python 3 and Docker, the pinned Jellyfin image (for ffmpeg) and the lab mpv image `cleanyfin-edl-lab:mpv` built from [`../edit-list-players/`](../edit-list-players/). Containers run with no network. Each run writes to a `.run-*` scratch directory here, which is git-ignored and can be deleted.

| Script | What it does |
| --- | --- |
| `join_lab.py` | The main matrix: the hard fixture's AAC movie played with both tracks and with video disabled, codec variants, and the PCM comparison. |
| `controls.py` | Control cases, including uniquely identifiable samples that catch a single wrong sample where a tone detector cannot. |
| `seek_control.py` | Start-position and seek cases. |
| `http_trial.py` | The same playback over loopback HTTP instead of local files. |

`receipt-2026-11-22.jsonl` is the output of the completed matrix on 2026-11-22 (image ids, fixture hash, mpv and FFmpeg versions, per-test results). The separate lossless-audio candidate described in the note was measured by hand and is **not** in that receipt or in the automated matrix yet.

Desktop mpv only. Nothing here measures Android, a television, or audio passed through undecoded.
