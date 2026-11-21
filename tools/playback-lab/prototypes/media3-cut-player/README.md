# Media3 cut player lab

Prototype for the shared synthetic hard fixture. It has been run once, on a Google TV emulator only (results in `knowledge-base/01-working/playback-paths-2026-11-21/README.md`); nothing here is a result for a physical device. A successful build and JVM test do not establish playback, splice accuracy, or compatibility on any particular device.

## Build

From this directory, with JDK 21, SDK platform 36 and build-tools 36.0.0 installed, and `ANDROID_HOME` pointing at the SDK:

```sh
./gradlew assembleDebug testDebugUnitTest
```

AGP 9.0.1, Gradle 9.3.1 and built-in Kotlin. APK: `app/build/outputs/apk/debug/app-debug.apk`. Min API 26, target API 36. This build does not download Android SDK components automatically.

## Install and run later on an owner-selected test device

Run the commands below **from the repository root**. Select a single authorized test device before using ADB (e.g. set `ANDROID_SERIAL`). Generate the fixture in a new directory; the app checks `source.mp4` against `plan.json` before playing any content.

```sh
python3 tools/playback-lab/hard-fixture.py /tmp/cleanyfin-fixture
adb install -r tools/playback-lab/prototypes/media3-cut-player/app/build/outputs/apk/debug/app-debug.apk
adb shell mkdir -p /sdcard/Android/data/lab.cleanyfin.cutplayer/files
adb push /tmp/cleanyfin-fixture/source.mp4 /tmp/cleanyfin-fixture/plan.json /sdcard/Android/data/lab.cleanyfin.cutplayer/files/
```

If the app-owned directory doesn't exist and shell cannot create it, open the app once to let Android create it. That startup will log a missing-fixture error and finish; then push the inputs. No app storage permission or network is needed.

Run one command at a time. Wait for the previous run to finish and use a **new** `run_id` each time; existing directories are not overwritten. `run_id` is optional (the app otherwise creates a UUID and logs its path under `CleanyfinCutLab`).

```sh
adb shell am start -n lab.cleanyfin.cutplayer/.MainActivity --es mode original --es seek none --es run_id original-01
adb shell am start -n lab.cleanyfin.cutplayer/.MainActivity --es mode cut --es seek none --es run_id cut-01
adb shell am start -n lab.cleanyfin.cutplayer/.MainActivity --es mode cut_pcm --es seek none --es run_id pcm-01
adb shell am start -n lab.cleanyfin.cutplayer/.MainActivity --es mode cut_pcm --es seek into --es run_id into-01
adb shell am start -n lab.cleanyfin.cutplayer/.MainActivity --es mode cut_pcm --es seek back --es run_id back-01
```

- `original` uses the uncut source as a positive control. `cut` (default) uses Media3's normal decoded PCM path, with no custom sample trimming. `cut_pcm` uses the same clipped playlist plus the PCM filter below.
- `into` starts at source 7.0 s in the control; in either filtered mode it snaps to source 10.520 s (suffix item at 0). `back` plays to one second after the splice in source time, seeks to source 5.0 s, then plays through to the end. `none` plays from the start.

## Pull and score

```sh
adb shell ls /sdcard/Android/data/lab.cleanyfin.cutplayer/files/runs/pcm-01/DONE
adb pull /sdcard/Android/data/lab.cleanyfin.cutplayer/files/runs/pcm-01 /tmp/pcm-01
python3 tools/playback-lab/analyze-capture.py --plan /tmp/cleanyfin-fixture/plan.json --audio /tmp/pcm-01/audio.pcm --json
```

`DONE` is written last, on normal completion, a reported error, or if the app leaves the foreground; its text must be `ended` for a completed playback run. Force-stop or process death may leave no `DONE`. Check `run.json` for errors, counts, seek requests, decoder names, and observed output flags (`null` if no output was initialized). `frames.csv` records renderer callbacks immediately **before** release to the surface: PTS in source microseconds, scheduled release time in monotonic nanoseconds, source frame index (blank for a nonmatching PTS), and `inside_mark`. It is not a display scan-out log. `audio.pcm` logs PCM bytes accepted by AudioTrack (stereo averaged to mono, already 48 kHz s16le), not the receiver/system mix. AudioTrack may itself discard queued audio on a seek.

The scorer does not accept CSV. For independent picture evidence, record the full picture without overlays, crop away any *external* letterbox bars while keeping the full barcode, then pass `--video /path/to/capture.mp4` along with `--audio` above. At normal speed with `seek=none`, the desired filtered frame sequence is `0..136,263..499`. `original` is expected to fail the cut scorer. `into` deliberately omits earlier kept content, and `back` deliberately repeats a segment and jumps backward; do not use the straight-through scorer's aggregate verdict for these runs. Check the CSV in release order against the requested lands in `run.json` and verify no marked frame in the filtered runs.

## What this tests

- Media3 **1.11.1**. `MediaItem.ClippingConfiguration` in a two-item ExoPlayer playlist (wrapped by `DefaultMediaSourceFactory` as `ClippingMediaSource`). Uses source microseconds derived from the plan's integer muxed PTS + time base (exclusive end is last marked PTS + one frame). Prefix end is exclusive; suffix runs to EOS. `startsAtKeyFrame=false` preserves the initial clipping discontinuity for decoder preroll; `relativeToDefaultPosition`, `relativeToLiveWindow`, and `allowUnseekableMedia` are false. `SeekParameters.EXACT` requests exact positions, not sync-frame snapping. Each playlist item has its own local seek coordinate; the timeline helper also computes concatenated filtered time.
- All modes disable tunneling and audio offload, and the output provider rejects compressed passthrough. `cut_pcm` uses a custom `AudioProcessor` driven by an `AudioSink` wrapper that supplies source PTS by subtracting the renderer's per-stream offset. The processor removes any marked PCM sample frames still present in decoder buffers (including the marked tail of a buffer straddling the prefix end). It resets pending buffers on seeks and uses fresh PTS per packet, not a wall clock. `pcmCutRemovedSamples` counts sample frames removed by the processor, not by Media3. This does **not** recover allowed samples Media3 discards upstream. For example, dropping the suffix-edge 1,024-sample AAC packet starting at source sample 504,832 would lose its final 896 allowed samples (18.67 ms); that is a predicted loss, not a measurement. AAC ringing, decoder-specific timestamps, black/held video at the splice, and real device output remain unmeasured.
EOF