# How to test playback on real devices: approaches and what each proves

**Living note · started 2026-11-25.** The practical "how do we actually test this" guide for Cleanyfin. Each approach lists what it can prove, what it cannot, what it costs and when to reach for it. Add a row whenever a new method is tried, including the ones that failed and why.

## The one thing to know first: copy protection (HDCP)

Streaming boxes such as the onn. 4K Pro encrypt their HDMI output with HDCP. A TV or monitor holds the keys and decrypts the picture *inside itself*; the only thing that leaves it is light on the panel. Anything that looks like a recorder, such as a USB HDMI capture adapter, receives the encrypted signal and shows **black**.

Observed 2026-11-25: the onn. 4K Pro fed straight into a UGREEN 15389 USB capture adapter (USB id 2b89:5389, MS2130 class) gave a plain dark frame. With no signal at all, that adapter shows its own UGREEN logo instead. A basic button-select HDMI switch passed no signal. Lowering the box to 1080p did not help, and Google TV's Developer options have no "HDCP checking" switch.

So, to see what a real box shows, you need a way that either records **before** the HDMI port or records **light** after the screen.

## Approaches

**The family box is closed to playback testing.** Use the private emulator until recording/cleanup and preservation of a signed-in profile across the 1.0.9 → 1.1.2 update both pass. The [TV runbook](common-formats-2026-11-24/04-tv-test-runbook.md) records those interlocks.

| Approach | Proves | Doesn't prove | Cost | Notes |
|---|---|---|---|---|
| **Android playback capture** (`tv-harness.py --r24 --projection-control`, private emulator only) | Records eligible audio from the ordinary mpv AudioTrack through Android’s playback-capture mix. On the tested emulator it reduced six channels to stereo with signed-16-grid values; use it for signature detection and calibrated seam measurements, not bit identity. | Picture, physical HDMI/HAL/speakers, passthrough, shared A/V timing or eligibility. Asking for 48 kHz does not rule out gain, downmix or resampling. | Free | A separate helper matches the Wholphin UID and media usage, obtains a run-scoped MediaProjection grant and stores only its synthetic PCM in its own private files. |
| **On-box compositor + mpv file** (`tv-harness.py --device --no-capture`) | Would observe Android screenrecord and decoded PCM written **instead of** ordinary audio output. | AudioTrack, HDMI, passthrough, or any refresh screenrecord skipped. | Free | Not qualified: on the fresh emulator Wholphin had no approved external directory for its WAV. No shell-created directory or storage workaround. |
| **Webcam pointed at a monitor** (`camera_witness.py`) | What actually appeared on a real screen: frame codes read off the panel, "no marked frame shown", plus a rough tone check through the mic. | Exact sound; frames shorter than the camera's exposure. The largest gap bounds every claim. | Free with a webcam you own | Records light, so HDCP doesn't apply. A pan/tilt/zoom webcam (Insta360) can aim itself through standard UVC controls. Good as an extra witness next to the debugging route. |
| **Phone slow-motion video of the screen** | Same as the webcam, at 120–240 fps. | Same as the webcam; also needs a manual file transfer. | Free | Fallback if no webcam. |
| **HDMI capture adapter + HDCP-stripping splitter** | The actual HDMI output, picture and sound. | Nothing extra beyond the HDMI port. | About $15–25 for the splitter, often a gamble | Only listings that explicitly say "HDCP bypass" or "stripper" stand a chance. "HDCP" alone in a title means it *keeps* protection (for example OREI HDS-102, "will not bypass HDCP"). Legal grey area: only ever record Cleanyfin's own synthetic test clips. |
| **Standalone recorder** (for example ClonerAlliance UHD Pro) | Records protected HDMI. | Exact sound, because it saves compressed audio. | About $150–200 | Not worth it for us. |
| **HDMI→SDI→HDMI** | Nothing. Licensed converters refuse protected input and show black. | — | — | Tried as an idea, not a path. |
| **Google TV emulator** (`tv-harness.py`, emulator mode) | Wholphin's behaviour, the plugin handoff, settings and transport, on Google's official Google TV image. | The box's real hardware decoders and audio path. | Free | Rehearsal before any real-box run. The harness boots its own private throwaway emulator, not T3's Device panel one. |
| **Desktop mpv in containers** (`format-matrix.py`, `check-plugin.py`) | Exact cut behaviour per format and server version, with picture and sound measured jointly. | Any TV app or device. | Free | Admission sweeps (R21–R24) run here. |
| **Listening and viewing clips** | What a viewer would notice: silence, pops, lip sync. | Statistical coverage. | Free | Make short side-by-side clips (ideal vs actual) so the owner can judge with eyes and ears, not just numbers. |

## Playback capture: first private Google TV test (2026-11-26)

[First run receipt](../../delivery/evidence/runs/tv-20261126T050345Z-704a49d96c/summary.json): the helper **did capture ordinary Wholphin audio** without opening another app’s storage. Wholphin 1.1.2 declared target SDK 37 and no explicit manifest capture policy. For uncut MKV/H.264/AC-3 on Jellyfin 12.4, Android reported a started `USAGE_MEDIA` AudioTrack for Wholphin’s UID and PID (48 kHz, mask `0x3f`); mpv reported 5.1 float. The helper negotiated stereo signed 16-bit PCM at 48 kHz and transferred all 4,489,216 recorded bytes, including nonzero audio. The app-op was reset and checked, the audio permission revoked, the helper uninstalled and the private emulator and scratch removed.

That first run did **not** establish bit identity: Jellyfin stopped reporting the item, no mpv EOF was retained, and no sample comparison was made in that run. An equal rate is not an equal mix. The 20-second picture clock is not the audio reference length either: this MKV decodes 960,000 stereo frames starting 240 frames before video zero. Its complete video-zero reference contains 959,760 frames; do not pad the tail. The MP4 E-AC-3 decoder starts at zero after codec skip and produces 959,744 frames, with no pre-roll to remove.

[Native-width rehearsal](../../delivery/evidence/runs/tv-20261126T051739Z-ece07d998e/summary.json): the helper tried 6, 5, 4 and 3 float input channels; all four index masks raised `IllegalArgumentException` on this API 36 image. Stereo float started (48 kHz). The movie take transferred all 13,557,760 declared bytes. On a separate channel-identification clip, the fitted stereo gains in FL, FR, FC, LFE, SL, SR order were approximately (1,0), (0,1), (.707107,.707107), (.5,.5), (.707108,0), (0,.707106). On six held-out tones the largest gain change was 0.00000245; all 12 tone windows had the same offset. That calibrates the mix, **not** the identity of six source channels. Neither take recorded mpv EOF. A host `TypeError` stopped the movie comparison after calibration; cleanup succeeded. No sample verdict can be inferred from this rehearsal.

**Movie comparison: the complete calibrated stereo prediction was not present byte for byte.** In the [frozen comparison run](../../delivery/evidence/runs/tv-20261126T052912Z-2768710d30/summary.json), the ordinary 5.1 float AudioTrack again yielded stereo float capture. All 147,611 nonzero movie values sampled every 13th frame fell on the signed-16 grid; so did all 47,641 sampled nonzero calibration values. Applying the measured gains and rounding to nearest-even predicted 99.77% of active held-out values exactly, versus about 50% for floor or truncation. This is a measured approximation of the mix, not an exact model of Android arithmetic.

The helper transferred all 13,549,568 movie bytes (1,693,696 stereo frames). No contiguous byte sequence matched the complete 959,760-frame prediction. The best whole-reference correlation was 0.999871 versus 0.997131 at a competing alignment. Start and middle windows favored a delay of 67,569 frames; the final window favored 60,878. The competing matches prevent a unique alignment, so no mismatch count or cause is assigned. Neither take saw mpv EOF: **coverage is unmeasured**, not a pass. This does not certify six individual channels, HDMI, speaker timing, cut behavior, or household readiness. The app-op reset, permission revocation, helper removal and private rig cleanup all succeeded.

The [ordinary MP4 retry](../../delivery/evidence/runs/tv-20261126T052322Z-ee0c3c6fae/summary.json) never played: the package-absence read ahead of APK installation exited 1. It measured no MP4 output.

The [completed ordinary MP4 rehearsal](../../delivery/evidence/runs/tv-20261126T053043Z-c4095b4e49/summary.json) passed the corrected source-clock check (959,744 frames, zero pre-roll) and reached playback. It saw 489 of 500 picture IDs, a maximum gap of 114.466 ms, 214 unreadable frames and no recognized mpv EOF. It stopped at the uncut control; no cut or layout qualified.

### What TV R24 should measure instead

**Keep bit-exact sound on desktop mpv.** Android playback capture is not a bit-exact sound tap on the tested emulator. Use its samples for three separate TV observations. The emulator’s timing is not a baseline for the household box.

1. **Marked material.** Use the dedicated [TV stereo witness](common-formats-2026-11-24/05-marked-detector.md#android-stereo-playback-mix), not the bit-exact desktop detector, on transient playback PCM. Its shared position code survives folding the original 5.1 layout to stereo. Receipts include both ear maps, the expected positive/negative on this route, and unknowns. A hit fails the zero-mark gate. A silent or incomplete take, an unidentified block, or unresolved mix energy is unmeasured, not zero.
2. **Picture coverage.** Read frame IDs and timestamps from Android screenrecord. Report marked IDs, missing IDs, unreadable frames and the longest gap. Do not infer a clean display refresh between observations. Screenrecord observes the compositor, not HDMI.
3. **Seams.** Report silence duration, transient/pop level and relative A/V timing separately from marked hits. Calibrate timing uncertainty and sound-level baselines from an uncut control on the *same device and route*. Compare local offsets around seams rather than assuming one delay for the entire take. A seam inside capture uncertainty is unresolved. Without a shared clock or independent A/V landmarks with unambiguous local alignment, do not score sync or publish a drift rate.

**Why score the mix instead of trusting a trace?** The desktop detector at `b002e7b` requires source-time code and an mpv AO packet trace covering the presented PCM. Android's mix supplies neither the original six channel vectors nor that packet trace. A demux/decoder PTS can identify data that was read or decoded and never reached the listener. Even AO PTS precedes Android's downmix, resampling and drops. Current player logs corroborate codec, driver and EOF, not source-sample presentation. A trace experiment remains useful for diagnosing an unknown; it cannot turn one into a negative. Earlier alternatives were:

- Generate separate stereo Dolby (2.0 AC-3 and E-AC-3) fixtures with code on L/R, so a 6→2 downmix cannot mix the code channels. These are new layouts; validate actual captured positives, negatives, gain/quantization, alignment and held-out sensitivity on each route. Do not transfer a 2.0 result to 5.1.
- Wholphin 1.1.2 pins `wholphin-extensions` 0.2.4. Its [JNI initialization](https://github.com/damontecres/wholphin-extensions/blob/v0.2.4/wholphin-mpv/src/main/jni/main.cpp#L59-L62) requests `terminal-default` and sets `msg-level=all=v`. Its [logger](https://github.com/damontecres/wholphin-extensions/blob/v0.2.4/wholphin-mpv/src/main/java/com/github/damontecres/wholphin/mpv/MpvLogger.kt) sends mpv info to Timber INFO, mpv verbose to DEBUG, debug/trace to VERBOSE. The emulator receipt already witnesses AO format and decoder messages in Wholphin's PID-filtered logcat. On synthetic playback only, try raising `demux` and `ad` to trace via the existing options editor. Verify the release log filter emits those messages and that they identify **actual audio-output sample counts and boundaries**, not just input compressed packets. Validate coverage against the downstream capture across seeks, resampling, drops and joins before claiming packet attribution. Neither a packet-complete trace nor household-box sensitivity has been demonstrated; the offline Dolby challenge measures the mix classifier, not that box.

## Real-box facts (onn. 4K Pro, read-only probe, 2026-11-25)

- Model `onn 4K Pro Streaming Device` (jarvis2), Android 14 (SDK 34), patch 2026-06-01, Amlogic S905X5M.
- **32-bit userspace** (`armeabi-v7a`): install `Wholphin-armeabi-v7a.apk`, not arm64.
- The read-only probe used Wireless debugging. Do not pair for playback yet. When authorized, use only the displayed explicit endpoints and enter the pairing code at the private prompt; no discovery or code in a command.
- Legacy unencrypted adb was also open on port 5555. Turn it off after testing.
- The family's own Wholphin (1.0.9) and Jellyfin Android TV (0.20.0) are installed. A future family run requires a signer-verified in-place update, never clear/uninstall/reinstall.
- Output is 4K at 59.94 Hz; `encoded_surround_output=1`.

The [signed-in update rehearsal](../../delivery/evidence/runs/tv-20261126T062510Z-327a0f1ff3/summary.json) verified a complete visible 1.0.9 baseline and installed 1.1.2 in place. After the update the emulator displayed **Select Server**, not authenticated Home. It showed the synthetic server, but did not prove preservation of the active user or settings. The only package-manifest difference was the expected version increase. No household playback or upgrade is released by this result.

## Outside tools worth knowing (owner-flagged, 2026-11-25)

| Tool | What it is | When it helps us | Cautions |
|---|---|---|---|
| [cleanyourtv](https://github.com/Gathin23/cleanyourtv) | Debloats Android and Google TV over Wi-Fi debugging, with a replacement launcher. | Confirms the Google TV pairing flow our harness uses. Its `tools/adb-closed.py` idea checks that the debug port is really closed after testing, and we added that to our restore proof. Its pure-Python ADB client may help where stock `adb` is blocked. | Never run its debloat or launcher on the family box; take ideas, not actions. |
| [REA (Reverse Engineer Anything)](https://github.com/morluto/rea) | An agent tool (MCP plus skills) for reverse engineering apps and native binaries, including Android. | Understanding closed behaviour we can't read as source: the box's audio path or vendor apps, a device's control protocol (for example webcam features beyond standard UVC), or why a closed player does something. For open-source apps (Wholphin, Jellyfin apps, mpv), read the source first. | Install and run it in a sandbox, and only from the official sources it lists. Reverse engineering for interoperability is generally fine; respect licences and terms, and never use it to defeat copy protection. |

## Rules that apply to every approach

- **Never announce the owner's identity to a device.** `adb pair` sends the debug key's label, by default `user@hostname`, and the box stores it under Paired devices. Use a dedicated key labelled `cleanyfin@lab` and an isolated adb server. Do the same for any client or device name. After testing, forget the PC under Paired devices. (Learned 2026-11-25: the default label was sent once and had to be forgotten on the box.)
- Only record Cleanyfin's own synthetic test clips, never streaming services or the family's media.
- Every change to a household device is recorded first and put back after, with a before/after proof. Turn debugging off when done.
- A gap in a recording means **unmeasured**, never passed.
- Each method's evidence is labelled with how it was measured. A camera witness or an on-box recording never poses as HDMI evidence, and desktop evidence never carries over to a TV.
