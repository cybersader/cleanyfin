# Jellyfin.Plugin.Cleanyfin

The Jellyfin plugin — a **Media Segments provider** that fetches community-tagged
content-filter segments from the cleanyfin API and exposes them to Jellyfin so
native clients can skip them (the Intro Skipper pattern, decision R02). It ships
**only** timestamps + type — never audio/video (R01).

Targets Jellyfin **10.11.x** (`net9.0`, `Jellyfin.Controller` 10.11.11).

## Build

From the **repository root**, mount both the plugin and its delivery-library reference:

```bash
docker run --rm -v "$PWD:/src" -w /src mcr.microsoft.com/dotnet/sdk:9.0 dotnet build plugin -c Release
```

Or with a local SDK: `dotnet build plugin -c Release`. Package both `Jellyfin.Plugin.Cleanyfin.dll` and `Cleanyfin.Delivery.dll` from `plugin/bin/Release/net9.0/`. The [disposable authenticated-handoff spike](../tools/playback-lab/README.md#authenticated-cut-list-handoff-disposable-101111-only) is disabled by default; it does not change ordinary playback.

### Per-viewer playback shelves (disposable 10.11.11 lab only)

A shelf is a separate Jellyfin home-video library. **Library access controls browsing; enrollment controls Play.** A new account with “access all libraries” sees the shelves and neutral sample titles. It receives no playable source or grant unless its own profile, device, native session, shelf and original are authorized. To hide another shelf, an administrator must set the viewer's explicit library list. The plugin never changes that list. Someone with ordinary access can still choose the original in its own library.

There is no settings UI or upstream fetch for shelves. In the lab, under Jellyfin's **plugin configurations directory** (`/config/plugins/configurations` in the pinned image), use `cleanyfin-shelves-v1/`:

| Operator input | Meaning |
|---|---|
| `snapshots/<original-item-id>.json` | Strict accepted snapshot v1. Bind the native server ID, item and media-source ID, full SHA-256, length, duration and reviewed presentation clock. Every annotation includes its semantic content digest. |
| `profiles/<viewer-id>/policy.json` | Strict private policy v1 for this viewer; `profileRef` must equal the lowercase, 32-hex native viewer ID. A selected required mute or no applicable cuts means **no offer**. |
| `profiles/<viewer-id>/device.txt` | Exact enrolled Jellyfin device ID, plain text. Not a login token. |
| `profiles/<viewer-id>/evidence-device.txt` | Lab declaration: app build SHA-256 and exact output mode, one per line. See the [evidence gate](../delivery/README.md#evidence-register). |

Schemas and examples are in `delivery/schema/` and `tests/Delivery.Contract.Tests/Fixtures/`. Write documents to a temporary sibling, then rename. Disable delivery while staging a change that must span multiple files atomically. Only the trusted operator and plugin should write this private tree. Set `LabOrigin` to the lab's loopback origin and base path, pin `LabSourceSha256` to the synthetic fixture, set `EnableAuthRangeLab=true`, and leave `EnableProductionEligibility=false` and `EnableLegacyRangeFixture=false`. The latter is a mutually exclusive, fixed-plan **diagnostic**, not another way to open a shelf. The lab allows up to eight profiles and eight snapshots. Do not enable this on a household server.

The plugin creates native shelves and writes only inert `.strm` locators in `shelves/`; the private `inventory.json` binds each opaque locator to its original, profile, input generation, and timeline. On startup and approximately every four seconds it reconciles local inputs. An administrator can also `POST /Cleanyfin/lab/reconcile`. A changed cut set gets a **new item**, unwatched at zero. Renewing a grant for the same timeline does not reset resume. No state is mirrored to the original. When Play or either grant leg encounters stale inputs it refuses immediately, without waiting for a catalogue scan. Already buffered bytes cannot be recalled.

**Supported source bounds for this experiment:** individual operator-controlled, read-only Linux `.mp4` file mounts under `/fixture-library/`, at most 64 MiB each; no host writer during verification or serving. Read-only directory mounts and mutable household movies are refused, not treated as immutable by pathname. On a new mount/inode/ctime/size version the plugin probes actual duration, track types and presentation origin with the pinned image's ffprobe and hashes the full file. The two tested clock anchors are required. For that verified MP4, the native source may describe the same container family by an alias such as `mov`, or by the FFmpeg comma list, and the device may advertise any member. This does not admit other formats, codecs, or bytes; native DirectPlay and the exact evidence tuple are still required. Ordinary serving remains Jellyfin's native static route. This is not protection against a malicious host writer or a hardware playback certification.

**What a viewer will not get:** no chapters (reject embedded chapters and set mpv `!no_chapters`), no trickplay, images, extras or scrub previews from the original (no copied metadata or extraction), no external subtitles and no alternate audio (reject nonmatching physical streams and sidecars; only H.264 + one AAC track). Native pointer image, trickplay-image and download routes are withheld. Titles are deliberately neutral, not a promise about the movie. External captions, downloads/offline use, arbitrary clients and remux/transcode retries are unsupported.

**Removal:** turn `EnableAuthRangeLab` and `EnableProductionEligibility` off, then as administrator `POST /Cleanyfin/lab/cleanup`. This drops inventory and matching owned files, not inputs, foreign files or the original library. Empty native shelf libraries can be removed in Jellyfin. Removing the plugin first leaves inert locator files, never a path to the original. Restore the plugin with the flag off to use its cleanup action. See [the disposable runner](../tools/playback-lab/README.md#per-viewer-shelf-lifecycle) and the [upkeep ledger](../.claude/32-UPKEEP-LEDGER.md).

## Install (once released)

Add a plugin repository in Jellyfin (Dashboard → Plugins → Repositories) pointing
at the published `manifest.json`, then install "Cleanyfin" and restart. Configure
the **cleanyfin API base URL** on the plugin's config page.

## How it works

`CleanyfinSegmentProvider` implements `IMediaSegmentProvider`. For each item,
Jellyfin calls `GetMediaSegments`, and the provider:
1. computes the release **fingerprint** — the OpenSubtitles-style **moviehash**
   (`osh:` + filesize/first+last-64KiB checksum) of the item's file (R04), with a
   `jf:ItemId` fallback if the file can't be read,
2. `GET`s `{ApiBaseUrl}/api/v1/segments?fp=...` from the cleanyfin server,
3. maps each returned segment to a `MediaSegmentDto` (start/end ticks = ms × 10000).

The plugin also exposes `GET /Cleanyfin/Fingerprint?itemId=...` so the marking
PWA can resolve the same fingerprint (the browser can't read file bytes).

For the write path, `POST /Cleanyfin/Segments` accepts a mark
(`{ itemId, startMs, endMs, category, severity, action, submitterId }`),
resolves the item's fingerprint + duration server-side, and **forwards** it to
`{ApiBaseUrl}/api/v1/segments` on the cleanyfin API (the live-insert path, R14),
proxying that API's status and body back to the caller. This keeps writes keyed
on the SAME fingerprint the provider later queries (R04).

## Honest limits (this slice)

- **Fingerprint = moviehash** (`osh:`…) so segments line up across anyone with
  the same file/rip (R04). Not yet handled: differently-encoded rips of the same
  title (needs a per-file calibration offset / audio-anchor — a later slice), and
  the `jf:ItemId` fallback still applies when a file can't be read.
- **No content-filter segment type exists** in Jellyfin, so every segment is
  emitted as `MediaSegmentType.Unknown`; the real category/severity/action lives
  in the cleanyfin DB (R14). Clients currently **skip** these (no native mute yet).
- **No per-profile enforcement** — segments are global per item; Jellyfin carries
  no per-user context in the provider pipeline (Spike A / R13). Per-profile
  filtering is the optional response-filtering proxy, a later slice.
- **No media delivery by the plugin:** the shelf verifier reads source bytes to hash them; only Jellyfin sends media to the player.
