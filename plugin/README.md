# Jellyfin.Plugin.Cleanyfin

The Jellyfin plugin — a **Media Segments provider** that fetches community-tagged
content-filter segments from the cleanyfin API and exposes them to Jellyfin so
native clients can skip them (the Intro Skipper pattern, decision R02). It ships
**only** timestamps + type — never audio/video (R01).

Targets Jellyfin **10.11.x** (`net9.0`, `Jellyfin.Controller` 10.11.11).

## Build

No local .NET needed if you have Docker:

```bash
docker run --rm -v "$PWD:/src" -w /src mcr.microsoft.com/dotnet/sdk:9.0 dotnet build -c Release
# -> bin/Release/net9.0/Jellyfin.Plugin.Cleanyfin.dll
```

Or with a local SDK: `dotnet build -c Release`.

## Install (once released)

Add a plugin repository in Jellyfin (Dashboard → Plugins → Repositories) pointing
at the published `manifest.json`, then install "Cleanyfin" and restart. Configure
the **cleanyfin API base URL** on the plugin's config page.

## How it works

`CleanyfinSegmentProvider` implements `IMediaSegmentProvider`. For each item,
Jellyfin calls `GetMediaSegments`, and the provider:
1. derives a fingerprint `"jf:" + ItemId`,
2. `GET`s `{ApiBaseUrl}/api/v1/segments?fp=...` from the cleanyfin server,
3. maps each returned segment to a `MediaSegmentDto` (start/end ticks = ms × 10000).

## Honest limits (this slice)

- **Fingerprint is a placeholder** (`jf:` + ItemId). Real cross-rip moviehash
  calibration (R04) is a later slice — segments only line up for the same Jellyfin
  item, not across different files/rips yet.
- **No content-filter segment type exists** in Jellyfin, so every segment is
  emitted as `MediaSegmentType.Unknown`; the real category/severity/action lives
  in the cleanyfin DB (R14). Clients currently **skip** these (no native mute yet).
- **No per-profile enforcement** — segments are global per item; Jellyfin carries
  no per-user context in the provider pipeline (Spike A / R13). Per-profile
  filtering is the optional response-filtering proxy, a later slice.
- **Metadata-only:** the plugin never touches media bytes, only timestamps.
