# How to test playback on real devices: approaches and what each proves

**Living note · started 2026-11-25.** The practical "how do we actually test this" guide for Cleanyfin. Each approach lists what it can prove, what it cannot, what it costs and when to reach for it. Add a row whenever a new method is tried, including the ones that failed and why.

## The one thing to know first: copy protection (HDCP)

Streaming boxes such as the onn. 4K Pro encrypt their HDMI output with HDCP. A TV or monitor holds the keys and decrypts the picture *inside itself*; the only thing that leaves it is light on the panel. Anything that looks like a recorder, such as a USB HDMI capture adapter, receives the encrypted signal and shows **black**.

Observed 2026-11-25: the onn. 4K Pro fed straight into a UGREEN 15389 USB capture adapter (USB id 2b89:5389, MS2130 class) gave a plain dark frame. With no signal at all, that adapter shows its own UGREEN logo instead. A basic button-select HDMI switch passed no signal. Lowering the box to 1080p did not help, and Google TV's Developer options have no "HDCP checking" switch.

So, to see what a real box shows, you need a way that either records **before** the HDMI port or records **light** after the screen.

## Approaches

| Approach | Proves | Doesn't prove | Cost | Notes |
|---|---|---|---|---|
| **Wireless debugging: on-box screen recording + player sound** (`tv-harness.py --device --no-capture`) | The real box, real Wholphin and real decoding. Picture from Android's own screen recorder; exact decoded sound from mpv's PCM output. | The last step out of the HDMI port (anything the box does to sound after the player, passthrough to a receiver). | Free | **Main route.** The player's video surface isn't protected, so screen recording works. The recorder only writes frames when the screen changes, so score by timestamps. The owner turns Wireless debugging on and types the pairing code; the harness records and restores every setting. |
| **Webcam pointed at a monitor** (`camera_witness.py`) | What actually appeared on a real screen: frame codes read off the panel, "no marked frame shown", plus a rough tone check through the mic. | Exact sound; frames shorter than the camera's exposure. The largest gap bounds every claim. | Free with a webcam you own | Records light, so HDCP doesn't apply. A pan/tilt/zoom webcam (Insta360) can aim itself through standard UVC controls. Good as an extra witness next to the debugging route. |
| **Phone slow-motion video of the screen** | Same as the webcam, at 120–240 fps. | Same as the webcam; also needs a manual file transfer. | Free | Fallback if no webcam. |
| **HDMI capture adapter + HDCP-stripping splitter** | The actual HDMI output, picture and sound. | Nothing extra beyond the HDMI port. | About $15–25 for the splitter, often a gamble | Only listings that explicitly say "HDCP bypass" or "stripper" stand a chance. "HDCP" alone in a title means it *keeps* protection (for example OREI HDS-102, "will not bypass HDCP"). Legal grey area: only ever record Cleanyfin's own synthetic test clips. |
| **Standalone recorder** (for example ClonerAlliance UHD Pro) | Records protected HDMI. | Exact sound, because it saves compressed audio. | About $150–200 | Not worth it for us. |
| **HDMI→SDI→HDMI** | Nothing. Licensed converters refuse protected input and show black. | — | — | Tried as an idea, not a path. |
| **Google TV emulator** (`tv-harness.py`, emulator mode) | Wholphin's behaviour, the plugin handoff, settings and transport, on Google's official Google TV image. | The box's real hardware decoders and audio path. | Free | Rehearsal before any real-box run. The harness boots its own private throwaway emulator, not T3's Device panel one. |
| **Desktop mpv in containers** (`format-matrix.py`, `check-plugin.py`) | Exact cut behaviour per format and server version, with picture and sound measured jointly. | Any TV app or device. | Free | Admission sweeps (R21–R24) run here. |
| **Listening and viewing clips** | What a viewer would notice: silence, pops, lip sync. | Statistical coverage. | Free | Make short side-by-side clips (ideal vs actual) so the owner can judge with eyes and ears, not just numbers. |

## Real-box facts (onn. 4K Pro, read-only probe, 2026-11-25)

- Model `onn 4K Pro Streaming Device` (jarvis2), Android 14 (SDK 34), patch 2026-06-01, Amlogic S905X5M.
- **32-bit userspace** (`armeabi-v7a`): install `Wholphin-armeabi-v7a.apk`, not arm64.
- Wireless debugging works: `adb pair <ip:pairing-port> <code>`, then `adb connect <ip:connect-port>`. The pairing screen can refresh its port and code; read them again if pairing fails. `adb mdns services` shows the current ports.
- Legacy unencrypted adb was also open on port 5555. Turn it off after testing.
- The family's own Wholphin (1.0.9) and Jellyfin Android TV (0.20.0) are installed. The harness refuses to touch an existing Wholphin.
- Output is 4K at 59.94 Hz; `encoded_surround_output=1`.

## Rules that apply to every approach

- Only record Cleanyfin's own synthetic test clips, never streaming services or the family's media.
- Every change to a household device is recorded first and put back after, with a before/after proof. Turn debugging off when done.
- A gap in a recording means **unmeasured**, never passed.
- Each method's evidence is labelled with how it was measured. A camera witness or an on-box recording never poses as HDMI evidence, and desktop evidence never carries over to a TV.
