# Playback lab

First test: given a start and end timestamp, remove that interval and nothing else. From the repository root on Linux or macOS with Python 3 and Docker:

```sh
python3 tools/playback-lab/check-cut.py
```

Docker downloads a pinned Jellyfin image on first use for its bundled FFmpeg. The test containers have no network, expose no ports and mount only the test's temporary directory. No Jellyfin account, household library, AI model, or private configuration is needed. The test removes its generated media when finished; Docker retains the cached image.

The six-second synthetic movie has three distinguishable pictures and tones. The middle two seconds are marked. The test makes a lossless reference edit, decodes both files, and checks that the output equals the original prefix plus suffix, byte for byte, separately for picture and sound. A missing allowed frame, an extra marked frame, or a changed audio sample fails the check.

This is a reference for what correct output looks like, **not a Cleanyfin playback implementation**. Its timestamps align with video frames and audio samples.

## Shared hard fixture: compare playback paths

The hard kit measures leaked and missing picture and sound against the same 20-second clip. Its one cut removes source frames 137–262 (5.480 to 10.520 s, end exclusive), inside both H.264 GOPs and AAC packets. The generator writes `source.mp4`, a lossless `reference.mkv`, and Kodi and mpv edit lists derived from `plan.json` (lab format v0, **not** a product contract). From the repository root:

```sh
python3 tools/playback-lab/hard-fixture.py NEW_DIRECTORY
python3 tools/playback-lab/analyze-capture.py --plan NEW_DIRECTORY/plan.json --video CAPTURE --audio CAPTURE --json
python3 tools/playback-lab/check-kit.py
```

Supply either or both capture options; pass the same recording to both to check both tracks. `--video` also accepts a directory of numbered images in one format, in capture order. Preserve the full picture so the frame barcode stays in view. Repeated frames are reported; backward jumps in source order fail. `--audio` accepts WAV or another media file, or raw `.pcm`/`.s16le` (48 kHz mono signed 16-bit little-endian). Audio times start at the first decoded sample; the detector reports 5 ms windows with a tone amplitude of at least 0.10 full scale and tolerates up to 5 ms of loss per neighbouring tone. This is a thresholded measurement, not sample-by-sample proof. `--json` adds the machine-readable result on the last line.

Passing the self-test says nothing about any real player until that player's output is captured and analyzed.

## Browser playback: original vs. filtered timeline

Run `python3 tools/playback-lab/serve-web.py`, open the printed loopback URL in Chromium, and choose **Run playback checks**. This generates a temporary compressed clip, a packaging index and an exact-source cut plan. The command serves only that temporary lab until Ctrl-C. There is no household library scan, edited movie on disk, or AI dependency.

The original control must show the red scene and its tone. The experimental player omits its entire encoded fragment from the browser's media buffer, preserves the blue and green sections and their tones, and maps a seek into the omitted scene to the start of green. The checks report sampled colors, dominant tones, duration and seek landing. They are **not** a proof of zero audio samples or compatibility with stock Jellyfin Web. Unsupported codecs, a wrong source, and unaligned timestamps must not quietly switch to unfiltered playback.

For an integration check in a **disposable loopback Jellyfin 10.11.11 lab only**: generate the bundle with `web-fixture.py NEW_DIRECTORY`; index its `Fixture.mp4` in the synthetic test library; put the generated HTML, JS and JSON assets under a separate lab path in the container web root. Load the page with `?item=<fixture item ID>`. It uses the passwordless, non-admin lab user `Viewer` to fetch the original through Jellyfin's direct stream endpoint. Do not configure an existing household server this way. This is a separate client, not a change to the stock player. See the [client evidence and constraints](../../knowledge-base/01-working/long-term-architecture-2026-10-07/10-client-boundary-and-browser-proof.md).
