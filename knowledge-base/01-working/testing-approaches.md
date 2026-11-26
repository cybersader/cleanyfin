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
| **Android playback capture** (`tv-harness.py --r24 --projection-control`, private emulator only) | Records eligible audio from the ordinary mpv AudioTrack through Android’s playback-capture mix. A full comparison against the uncut source tests sample identity for that run. | Picture, physical HDMI/HAL/speakers, passthrough, shared A/V timing or eligibility. Asking for 48 kHz does not rule out gain, downmix or resampling. | Free | A separate helper matches the Wholphin UID and media usage, obtains a run-scoped MediaProjection grant and stores only its synthetic PCM in its own private files. |
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

## Real-box facts (onn. 4K Pro, read-only probe, 2026-11-25)

- Model `onn 4K Pro Streaming Device` (jarvis2), Android 14 (SDK 34), patch 2026-06-01, Amlogic S905X5M.
- **32-bit userspace** (`armeabi-v7a`): install `Wholphin-armeabi-v7a.apk`, not arm64.
- The read-only probe used Wireless debugging. Do not pair for playback yet. When authorized, use only the displayed explicit endpoints and enter the pairing code at the private prompt; no discovery or code in a command.
- Legacy unencrypted adb was also open on port 5555. Turn it off after testing.
- The family's own Wholphin (1.0.9) and Jellyfin Android TV (0.20.0) are installed. A future family run requires a signer-verified in-place update, never clear/uninstall/reinstall.
- Output is 4K at 59.94 Hz; `encoded_surround_output=1`.

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
