# Exact audio at a cut: experiment scripts

These scripts compare what real mpv outputs for a cut list against the ideal: decode the uninterrupted original once, then delete exactly the marked samples. The comparison is sample by sample on both channels with no alignment adjustment. Findings are written up in [`knowledge-base/01-working/exact-audio-2026-11-22/01-audio-at-the-join.md`](../../../../knowledge-base/01-working/exact-audio-2026-11-22/01-audio-at-the-join.md).

They need Python 3 and Docker, the pinned Jellyfin image (for ffmpeg) and the lab mpv image `cleanyfin-edl-lab:mpv` built from [`../edit-list-players/`](../edit-list-players/). Containers run with no network; the optional HTTP trial shares an isolated loopback. The scripts remove their temporary captures.

| Script | What it does |
| --- | --- |
| `join_lab.py` | The main matrix: the hard fixture's AAC movie played with both tracks and with video disabled, codec variants, and the PCM comparison. |
| `controls.py` | Control cases, including uniquely identifiable samples that catch a single wrong sample where a tone detector cannot. |
| `seek_control.py` | Start-position and seek cases. |
| `http_trial.py` | The same playback over loopback HTTP instead of local files. |

`receipt-2026-11-22.jsonl` preserves the original exploratory matrix. `joint-receipt-2026-11-22.json` is the fresh AAC straight-cut receipt from `run-mpv.py --receipt PATH`: all 374 kept pictures and 718,080 stereo samples matched, with neither marked picture nor sound. Video-disabled runs in the exploratory matrix are diagnostic only.

Run `python3 tools/playback-lab/prototypes/exact-audio/join_lab.py --suite identities` to measure the lossless pair. Stock playback of the `[0.240, 0.440)` cut must **fail**: source sample 21,119 replaces output sample 11,519 and total PCM is one sample short. The experimental EDL shifts nonzero video boundaries a quarter sample earlier and audio boundaries a quarter sample later in separate partitions of that same source. It passed with 495/495 pictures and 950,400/950,400 exact stereo samples on the single cut, and 314/314 pictures and 602,880/602,880 exact stereo samples across 57 kept ranges. The candidate is a FLAC lab control, not a renderer change or an AAC recommendation.

Desktop mpv only. Nothing here measures Android, a television, or audio passed through undecoded.
