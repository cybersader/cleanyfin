# Desktop edit-list playback lab

**Result:** On this local Linux fixture, mpv kept exactly the expected picture frames but changed the first 448 stereo PCM frames after the cut. Kodi exposed marked source frame 255. Neither cut produced a detectable 880 Hz tone in the capture.

## Reproduce

Run from the repository root. Build-time network is for Debian packages and the shared lab's pinned FFmpeg image; playback and scoring containers use `--network none`. The runners require Linux, Docker, and Python 3 (standard library only). Each creates a fresh fixture and deletes its temporary captures at exit.

```sh
docker pull jellyfin/jellyfin@sha256:aefb67e6a7ff1debdd154a78a7bbb780fd0c873d8639210a7f6a2016ad2b35db
docker build --target mpv -t cleanyfin-edl-lab:mpv tools/playback-lab/prototypes/edit-list-players
python3 tools/playback-lab/prototypes/edit-list-players/run-mpv.py
docker build --target kodi -t cleanyfin-edl-lab:kodi tools/playback-lab/prototypes/edit-list-players
python3 tools/playback-lab/prototypes/edit-list-players/run-kodi.py
```

Observed local image IDs (Docker config digests, not registry tags):

| Player | Tested version | Image ID |
|---|---|---|
| mpv | v0.40.0, Debian `0.40.0-3+deb13u1` | `sha256:dd6013a58b8e0132cd943f2b5dab481516b10dc56350b21dc3a490861a8f295c` |
| Kodi | 21.2, Debian `2:21.2+dfsg-4` | `sha256:6fb30fbfed02cafe029a625268e27350e0cf175d8a45a7c042266347667f2d10` |

The Debian base is pinned; apt packages resolve at build time. These results do not test the mpv 0.42 / Kodi 21.3 source versions in the research note. Docker reported ~1.76 GB of image disk use including the shared base. Completed runs peaked at ~398 MB of mpv captures or ~164 MB of Kodi captures, separately.

## Measurements

The 25 fps, H.264/AAC source marks frames 137–262 and the 880 Hz tone from 5.480 to 10.520 s. The shared scorer expects a **full cut** on every run, so controls and intentional partial/backward seeks can fail by design. Its audio rule detects tones at ≥0.1 full scale in 5 ms windows; a zero is not proof of zero audio samples.

| Run | Marked presentations | Detected 880 Hz | Score summary (verbatim) |
|---|---:|---:|---|
| mpv source control | 126 (137–262) | 5040 ms | `FAIL: video and audio` |
| mpv EDL, linear | 0 | 0 ms | `FAIL: video and audio` |
| mpv EDL, start at 5.280 s | 0 | 0 ms | `FAIL: video and audio` |
| mpv EDL, source-time mapped seek + backward seek | 0 | 0 ms | `FAIL: video and audio` |
| mpv source, sample-index mute (audio-only) | — | 0 ms | `FAIL: audio` |
| Kodi source control | 79 (6 unique marked IDs) | 5040 ms | `FAIL: video and audio` |
| Kodi local `source.edl` | 12 (all source ID 255) | 0 ms | `FAIL: video and audio` |

- **mpv linear and start:** separate untimed real output runs (`--vo=image` lossless PNG with audio off; `--ao=pcm` stereo 48 kHz signed 16-bit WAV with video off) avoid one dump clock affecting the other. Control delivered all 500 frame IDs without duplicates or unreadable frames. The cut delivered 374, with none missing, repeated, or unreadable; the start variant missed only the intended prefix 0–131. Both EDL runs lost 10 ms of detected 660 Hz at the splice; the 10 ms initial 440 Hz deficit in the linear run was also present in the unedited control. Cut PCM length was exactly 718,080 frames. Compared with the retained ranges of the same-player control (without the control's 512 extra AAC tail samples), only frames 263040–263487 differed (~9.33 ms), immediately after the splice.
- **mpv seek:** no removed source interval exists on the edited clock. The Lua test maps source 8.000 s to its splice at edited 5.480 s (source 10.520 s), then seeks backward from edited 5.880 s to 5.280 s. It logs both restart events and captures PNG+WAV in one playback because PCM-only playback runs past the seek trigger. This setup checks marked content, not real-time sync or exact seek landing; reordering and repeated kept frames are expected after the back-seek.
- **mpv mute:** `aeval` gates both channels by decoded sample index in a linear source run; all 241,920 PCM frames in the marked interval were zero. The same initial 440 Hz deficit existed in the control. Filter behavior after a seek was not measured.
- **mpv file failure:** opening a nonexistent `.mpv.edl` or a valid-header EDL with `not-a-time` in its start field produced a file/parse error, exit 2, and no WAV; neither played the source. This checks direct EDL launch, not external caller fallback logic.
- **Kodi:** fresh Xvfb/llvmpipe 1280×720 display and null PulseAudio sink per run; FFmpeg recorded the entire display with FFV1 and sink monitor with PCM. `x11grab` requested 50 fps; recorded frame count divided by audio capture duration was ~16.5 fps control and ~17.4 fps cut. The control missed many frame IDs, so omissions and duplicate counts cannot establish what Kodi rendered between captures. The valid ID 255 is positive marked-picture evidence. Startup/teardown and sink buffering stayed in the recording; the control already lost some allowed tone, so the Kodi audio result is **no detected marked tone**, not proven exact preservation.

These are desktop Linux, local synthetic-file measurements. No Android TV, Jellyfin stream, household device, native display timing, or hardware audio output was tested.
