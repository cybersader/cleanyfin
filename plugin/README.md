# Jellyfin.Plugin.Cleanyfin

The Jellyfin plugin — a **Media Segments provider** that fetches community-tagged
content-filter segments from the cleanyfin API and exposes them to Jellyfin so
native clients can skip them (the Intro Skipper pattern, decision R02). It ships
**only** timestamps + type — never audio/video (R01).

Builds for one Jellyfin line at a time, chosen by the `JellyfinTarget` property in the csproj:

| `-p:JellyfinTarget=` | Jellyfin.Controller | Framework | SDK image | Lab result |
|---|---|---|---|---|
| `10.11` (default) | 10.11.11 | `net9.0` | `mcr.microsoft.com/dotnet/sdk:9.0` | all six `check-plugin.py` cases pass on 10.11.11 (2026-11-25) |
| `12.4` | 12.4.0 | `net10.0` | `mcr.microsoft.com/dotnet/sdk:10.0` | all six `check-plugin.py` cases pass on 12.4.0 (2026-11-25) |

A build for one line refuses the lab gate on any other server version. Player evidence is limited to the exact tuples in `delivery/evidence/register-v1.json`; the desktop mpv rows do not qualify TV or Wholphin playback. The release manifest (`build.yaml`, `manifest.json`) still declares only the 10.11 ABI.

## Build

From the **repository root**, mount both the plugin and its delivery-library reference:

```bash
docker run --rm -v "$PWD:/src" -w /src mcr.microsoft.com/dotnet/sdk:9.0 dotnet build plugin -c Release
docker run --rm -v "$PWD:/src" -w /src mcr.microsoft.com/dotnet/sdk:10.0 dotnet build plugin -c Release -p:JellyfinTarget=12.4
```

Or with a local SDK: `dotnet build plugin -c Release`. Package `Jellyfin.Plugin.Cleanyfin.dll`, `Cleanyfin.Delivery.dll` and `Cleanyfin.Marking.dll` from `plugin/bin/Release/net9.0/` (or `net10.0/` for 12.4). The [disposable authenticated-handoff spike](../tools/playback-lab/README.md#authenticated-cut-list-handoff-disposable-lab-only) is disabled by default; it does not change ordinary playback.

### Per-viewer playback shelves (disposable 10.11.11 / 12.4.0 lab only)

A shelf is a separate Jellyfin home-video library. **Library access controls browsing; enrollment controls Play.** A new account with “access all libraries” sees the shelves and neutral sample titles. It receives no playable source or grant unless its own profile, device, native session, shelf and original are authorized. To hide another shelf, an administrator must set the viewer's explicit library list. The plugin never changes that list. Someone with ordinary access can still choose the original in its own library.

There is no settings UI or upstream fetch for shelves. In the lab, under Jellyfin's **plugin configurations directory** (`/config/plugins/configurations` in the pinned image), use `cleanyfin-shelves-v1/`:

| Operator input | Meaning |
|---|---|
| `snapshots/<original-item-id>.json` | Strict accepted snapshot v1. Bind the native server ID, item and media-source ID, full SHA-256, length, duration and reviewed presentation clock. Every annotation includes its semantic content digest. |
| `profiles/<viewer-id>/policy.json` | Strict private policy v1 for this viewer; `profileRef` must equal the lowercase, 32-hex native viewer ID. A selected required mute or no applicable cuts means **no offer**. |
| `profiles/<viewer-id>/device.txt` | Exact enrolled Jellyfin device ID, plain text. Not a login token. |
| `profiles/<viewer-id>/evidence-device.txt` | Lab declaration: app build SHA-256 and exact output mode, one per line. See the [evidence gate](../delivery/README.md#evidence-register). |
| `evidence-register-v1.json` | Reviewed register installed from `delivery/evidence/register-v1.json`, with rows for the exact deployed build and player tuple. Missing, invalid or out-of-build evidence means no filtered offer. |

Schemas and examples are in `delivery/schema/` and `tests/Delivery.Contract.Tests/Fixtures/`. Write documents to a temporary sibling, then rename. Disable delivery while staging a change that must span multiple files atomically. Only the trusted operator and plugin should write this private tree. Set `LabOrigin` to the lab's loopback origin and base path, pin `LabSourceSha256` to the synthetic fixture, set `EnableAuthRangeLab=true`, and leave `EnableProductionEligibility=false` and `EnableLegacyRangeFixture=false`. The latter is a mutually exclusive, fixed-plan **diagnostic**, not another way to open a shelf. The lab allows up to eight profiles and eight snapshots. Do not enable this on a household server.

The plugin creates native shelves and writes only inert `.strm` locators in `shelves/`; the private `inventory.json` binds each opaque locator to its original, profile, input generation, and timeline. On startup and approximately every four seconds it reconciles local inputs. An administrator can also `POST /Cleanyfin/lab/reconcile`. A changed cut set gets a **new item**, unwatched at zero. Renewing a grant for the same timeline does not reset resume. No state is mirrored to the original. When Play or either grant leg encounters stale inputs it refuses immediately, without waiting for a catalogue scan. Already buffered bytes cannot be recalled.

**Supported source bounds for this experiment:** individual operator-controlled, read-only Linux `.mp4` file mounts under `/fixture-library/`, at most 64 MiB each; no host writer during verification or serving. Read-only directory mounts and mutable household movies are refused, not treated as immutable by pathname. On a new mount/inode/ctime/size version the plugin probes actual duration, track types and presentation origin with the pinned image's ffprobe and hashes the full file. The two tested clock anchors are required. For that verified MP4, the native source may describe the same container family by an alias such as `mov`, or by the FFmpeg comma list, and the device may advertise any member. This does not admit other formats, codecs, or bytes; native DirectPlay and the exact evidence tuple are still required. Ordinary serving remains Jellyfin's native static route. This is not protection against a malicious host writer or a hardware playback certification.

**What a viewer will not get:** no chapters (reject embedded chapters and set mpv `!no_chapters`), no trickplay, images, extras or scrub previews from the original (no copied metadata or extraction), no external subtitles and no alternate audio (reject nonmatching physical streams and sidecars; only H.264 + one AAC track). Native pointer image, trickplay-image and download routes are withheld. Titles are deliberately neutral, not a promise about the movie. External captions, downloads/offline use, arbitrary clients and remux/transcode retries are unsupported.

**Removal:** turn `EnableAuthRangeLab` and `EnableProductionEligibility` off, then as administrator `POST /Cleanyfin/lab/cleanup`. This drops inventory and matching owned files, not inputs, foreign files or the original library. Empty native shelf libraries can be removed in Jellyfin. Removing the plugin first leaves inert locator files, never a path to the original. Restore the plugin with the flag off to use its cleanup action. See [the disposable runner](../tools/playback-lab/README.md#per-viewer-shelf-lifecycle) and the [upkeep ledger](../.claude/32-UPKEEP-LEDGER.md).

## Household marking (slice 2)

Marks are made, refined and approved inside the household, in this plugin (decision R16). Two pages are served on Jellyfin's own origin, so a phone or desktop browser needs no CORS setting and no second server:

| Page | Who | What it does |
|---|---|---|
| `<server>/Cleanyfin/marking/capture` | anyone signed in, on a phone | **Mark**, **Start/End** and four quick categories while the TV plays. Each press becomes a [capture-v1](../delivery/schema/capture-v1.schema.json) and is sent at once. The browser keeps an IndexedDB outbox; a press made offline is sent when the network returns, and leaves the outbox only when the server has it. Position is read from `GET /Sessions` for the signed-in person's own playback; the TV is not paused (R18). |
| `<server>/Cleanyfin/marking/review` | markers and reviewers, on a desktop | Lists an item's captures and marks, plays the original through Jellyfin's ordinary static stream, steps one frame (`,` `.`) or 100 ms (`[` `]`), sets the first and last affected frame (`I`, `O`), category (`1`–`9`), severity (`S`), action, then proposes (`Enter`), accepts (`A`, reviewers) and publishes. |

Sign-in is Jellyfin's own `AuthenticateByName`. The API under `/Cleanyfin/marking/` (`me`, `captures`, `items`, `items/{id}`, `marks`, `marks/{ref}` + `accept` / `reject` / `withdraw`, `items/{id}/publish`, `audit`, `timing`) runs every call as the authenticated Jellyfin user, read through `IAuthorizationContext` under Jellyfin's `[Authorize]`. Request bodies carry values only (strict, closed: `MarkingRequests` in `Cleanyfin.Marking`); a body naming an actor, author or state is refused, and API keys are refused because they name no person. Every role check is server-side in `MarkingService`.

**Roles.** With no governance file, Jellyfin administrators are `admin` + `reviewer` and everyone else is a `viewer`, who may flag a moment (a capture) and nothing more. Once `cleanyfin-marking-v1/governance.json` ([household-governance-v1](../delivery/schema/household-governance-v1.schema.json)) exists it is authoritative: members by `jf:user:<32-hex Jellyfin user id>`, roles, profiles and `selfApproval` (R17; `false` requires a second reviewer). A `suggester` (a household compute service signed in as its own Jellyfin user) may add `service` captures only.

**Storage.** Under Jellyfin's plugin configurations folder (`/config/plugins/configurations` in the official image), beside the shelves and outside the versioned install folder: `cleanyfin-marking-v1/` holds `captures.jsonl`, `marks.jsonl`, the hash-chained `audit.jsonl` and `audit.head` (count and digest of the newest line, so dropping the newest lines is evident too), plus the optional `governance.json`, `taxonomy.json` and `review-timing-v1.json`. The store is reopened and re-verified on every request; an edited, removed or truncated line refuses every call (`409`) until it is restored. Back up by copying the folder. This detects accidents; it is not protection against whoever controls the disk.

**Publishing** writes `cleanyfin-shelves-v1/snapshots/<item>.json`, the file the shelf resolver already reads; nothing downstream knows marking exists. A mark is bound to one **edition** of the file: the full SHA-256 of its bytes plus the stream layout ffprobe reads from them (`bind:<item>:<edition digest>`; [design](../knowledge-base/01-working/marking-foundation-2026-11-24/02-household-file-binding.md)).

**Household file check.** Any local, single-file video in a library can be bound. The first use queues one background job that probes and hashes the whole file (one file at a time, below-normal priority; set `EditionHashBytesPerSecond` in the plugin configuration to pace it on a busy NAS). Until it finishes, drafting, publishing and filtered playback refuse with `VerificationPending` and the review page shows progress; nothing waits on the hash. The result is kept in `cleanyfin-editions-v1/<item>.json` ([edition-record-v1](../delivery/schema/edition-record-v1.schema.json)), valid only while the file's inode, size, mtime and ctime are exactly as hashed. Every use re-checks those signals; any change refuses at once and re-hashes. The same bytes and layout give the same binding back (a touch); different ones are a new edition: the filtered item is withdrawn, earlier marks are shown as "FILE CHANGED, review again", and publishing refuses (`FileChanged`) until each is withdrawn or rejected. Marks are never moved to a new file. Not bindable: `.strm`/remote items, stacked parts, alternate versions, external audio files, and files whose streams do not start on one exact millisecond clock (`UnsupportedSourceClock`). The lab's pinned fixture goes through the same rule with a stricter verifier (`PinnedAsset`).

**Exact boundaries need a measured browser.** The review page reads the presented frame's `requestVideoFrameCallback` media time, maps it through the binding's video anchor and checks it is the frame it asked for. It enables Set start / Set end / Propose only when `review-timing-v1.json` (built in from `delivery/evidence/`, overridable in the marking folder) has a passing row for this browser engine, major version, platform, headless/headed mode and the item's frame rate; anywhere else it says so and refuses. Measured so far: Chromium 151, Linux, headless, 25 fps, on the barcode fixture (`tools/playback-lab/check-plugin.py --case marking`). A start is the first affected frame's start (rounded down to ms); an end is the start of the frame after the last affected one (rounded up), so a boundary never keeps part of a marked frame.

Not yet: captures on a filtered item (refused, `FilteredItemNotSupported`, until a plan revision can be mapped back), pause-to-mark, Media Segments on the review timeline, MCF/EDL import and a governance editor. Filtered *playback* of a household file still needs a measured evidence tuple for its stream layout (binding does not).

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
