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

## Authenticated cut-list handoff (disposable 10.11.11 only)

Run `python3 tools/playback-lab/check-plugin.py --case auth-range` from the repository root. Prerequisites: Docker, Python 3, the pinned Jellyfin image, `mcr.microsoft.com/dotnet/sdk:9.0` and the [desktop mpv lab image](prototypes/edit-list-players/README.md). The SDK restores build dependencies; the server and player share only a private loopback network namespace. No host port is published. The runner owns the synthetic source, hashes it and mounts that **one file** read-only. Do not substitute a mutable movie directory. On success the server and working data are removed; on failure the stopped container name and private local directory are printed. That directory includes Jellyfin's disposable authentication database; do not publish it.

The fixture-only issue endpoint is off by default. In the lab the descriptor and source have different short-lived bearer handles. Anonymous possession works until expiry or revocation; it is not proof of possession of the enrolled device. The plugin checks the viewer, device token hash, session, library rights, exact source and plan on each leg. Only Jellyfin's native static-file action sends movie bytes. At stock logging levels 10.11.11 printed the **native viewer token** at logout (`Emby.Server.Implementations.Session.SessionManager`, Information). The runner sets only that category to Warning before startup and scans server logs for all native test tokens and both capability values. HTTP logging remains at its defaults. Before considering a proxy, debug logging or another player, redact entire capability URLs (including paths), queries and auth headers at every layer. Completed reads and buffered bytes cannot be revoked.

A successful **handoff** does not certify editing fidelity. The runner prints the unchanged scorer verdict and compares stereo PCM against the trimmed unfiltered control. A kept-tone measurement can fail even when every HTTP assertion passes and no marked frames or tone are detected. No PlaybackInfo override, pointer library, device eligibility or household playback is provided by this case.

### One-pointer falsifier

Run `python3 tools/playback-lab/check-plugin.py --case pointer-refresh` with the same prerequisites. It creates **one** plugin-owned inert pointer, refreshes only disposable fixture folders/items, and compares the ordinary original's response bytes (apart from the fresh play-session nonce) with the flag off. The simulated Wholphin POST exercises native 10.11.11 negotiation on the original, not a probe or client impersonation at the media URL. A real server ffprobe must receive 410. The desktop control and cut use the unchanged frame/tone scorer. A successful grant, refresh and restart test is not an Android presentation measurement or a client-support claim.

On the Android 16 `cleanyfin-test` emulator, unmodified Wholphin 1.1.2/MPV chose DirectPlay on plugin build `6222440e0ee3f0f3be5e57108da07834f46b909f05508ca7ab5f487c43646f95`: pointer POST 200 → list GET 200 → native original range 206. The original control showed 65 marked presentations. The straight cut showed zero marked and 193 distinct kept frames at 15.4 fps; 35 screencaps at 1.85 Hz contained no red. Five-second seek controls showed source frames 71 → 301 → 0. Withheld projection, original ACL denial and flag-off each returned `NoCompatibleStream`. A 3 s grant did not recall buffered bytes; replay made a new POST. After a server restart the same handoff succeeded but every recorded frame was unreadable. Wholphin then timed out loading the shelf in Exo mode; no real Exo playback or plugin-absent trial was completed. No TV sound, frame-complete join or physical hardware evidence.

### Per-viewer shelf lifecycle

Run `python3 tools/playback-lab/check-plugin.py --case shelf-isolation`. Two independent policies over the **same original** omit either `[137,263)` or `[137,200)` source frames. The latter oracle deliberately scores picture only: its retained interval contains some of the 880 Hz tone, so the first cut's audio oracle would be false for it. The scorer is unchanged. The native progress, stop, watched, browse and restart assertions use filtered item IDs, not source-time conversion. The runner exercises both a default all-folders account and explicit folder lists; neither changes enrollment. It checks same-size replacement with the same duration, inspection for grants/media output, and byte-identical originals after cleanup. See the [operator input layout and limits](../../plugin/README.md#per-viewer-playback-shelves-disposable-101111-lab-only). No new client or household support is claimed.

## Browser playback: original vs. filtered timeline

Run `python3 tools/playback-lab/serve-web.py`, open the printed loopback URL in Chromium, and choose **Run playback checks**. This generates a temporary compressed clip, a packaging index and an exact-source cut plan. The command serves only that temporary lab until Ctrl-C. There is no household library scan, edited movie on disk, or AI dependency.

The original control must show the red scene and its tone. The experimental player omits its entire encoded fragment from the browser's media buffer, preserves the blue and green sections and their tones, and maps a seek into the omitted scene to the start of green. The checks report sampled colors, dominant tones, duration and seek landing. They are **not** a proof of zero audio samples or compatibility with stock Jellyfin Web. Unsupported codecs, a wrong source, and unaligned timestamps must not quietly switch to unfiltered playback.

For an integration check in a **disposable loopback Jellyfin 10.11.11 lab only**: generate the bundle with `web-fixture.py NEW_DIRECTORY`; index its `Fixture.mp4` in the synthetic test library; put the generated HTML, JS and JSON assets under a separate lab path in the container web root. Load the page with `?item=<fixture item ID>`. It uses the passwordless, non-admin lab user `Viewer` to fetch the original through Jellyfin's direct stream endpoint. Do not configure an existing household server this way. This is a separate client, not a change to the stock player. See the [client evidence and constraints](../../knowledge-base/01-working/long-term-architecture-2026-10-07/10-client-boundary-and-browser-proof.md).
