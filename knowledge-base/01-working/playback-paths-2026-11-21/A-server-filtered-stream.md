# Path A: the server delivers an already-filtered stream

Research notes, 2026-11-21. Source reading only against `jellyfin/jellyfin` v10.11.11, `jellyfin/jellyfin-web` v10.11.11 and `jellyfin/jellyfin-androidtv` v0.20.0. Nothing here was run. Line-level references were not returned and still need to be pinned before anyone relies on a claim.

**Verdict: viable with limits.** It works as an opt-in "clean version" of an item, not as a transparent default for stock clients.

## Findings

| Topic | Finding | Status |
| --- | --- | --- |
| Extension point | `IMediaSourceProvider` exists. `GetMediaSources(item, ct)` takes no user, so a provider source is global per item. | VERIFIED-FROM-SOURCE (`IMediaSourceProvider.cs`) |
| Source ordering | `MediaSourceManager.GetPlaybackMediaSources` appends provider sources after the real file. `MediaInfoHelper.SortMediaSources` puts a direct-playable `File` source first. | VERIFIED-FROM-SOURCE |
| Default selection | Web uses `MediaSources[0]` unless a version is chosen (`playbackmanager.js`). Android TV 0.20.0 prefers the source whose id equals the item id; the legacy player is the default because `playback_new` is false. | VERIFIED-FROM-SOURCE |
| Consequence | The original stays the default, so a viewer silently gets the unfiltered movie. Protection needs the original hidden from that profile, for example a separate strm-pointer library with per-user library access. | INFERRED |
| Per-viewer choice | Only library access can pick the version per profile, since the provider has no user. | INFERRED |
| Jellyfin HLS | The playlist is built from keyframes or equal-length segments with no `EXT-X-DISCONTINUITY` (`DynamicHlsPlaylistGenerator.cs`). Each segment URL carries `runtimeTicks`, which feeds ffmpeg `-ss`; segments come from one ffmpeg run using `-copyts`. Core cannot omit ranges, so the plugin needs its own controller and playlist. | VERIFIED-FROM-SOURCE |
| Cuts inside a GOP | Copying whole GOPs leaks marked frames. Boundary GOPs must be re-encoded to keep surrounding material; audio packets need the same care. | INFERRED, consistent with the lab |
| Remote and HLS sources | Web direct-plays a remote Http source through `mediaSource.Path`. `SupportsDirectStream` is forced false for paths containing ".m3u". The newer Android TV resolver drops anything that is not `Protocol==FILE && !isRemote`. | VERIFIED-FROM-SOURCE |
| Resume and progress | Reported positions would be on the filtered timeline, which would corrupt resume on the original version. | INFERRED |
| Failure mode | If the plan or ffmpeg fails, the endpoint must return an error and never fall back to the source. | INFERRED (design requirement) |

## Prior art

Nothing found that serves an edited stream (web search). CleanCut makes a separate edited copy. jellyfin-whisper bakes filtered audio tracks into MKV files. JellyFilter, jellyfin-plugin-contentfilter and PlexFilter with PlexAutoSkip are sidecar skip/mute tools.

## Smallest prototype

In a disposable 10.11.11 container, build a plugin controller `/Cleanyfin/hls/{id}/main.m3u8` that uses ffmpeg to copy whole GOPs and re-encode only the boundary GOPs, producing fMP4 segments on a filtered timeline. Add one strm pointer item in a test library. Run the existing red/880 Hz detector in Web and on an Android TV emulator (not the household device). Measure leaked frames, preserved neighbours, seek and resume behaviour, CPU, and behaviour when the plan is missing.

Estimated effort: 2 to 4 weeks for a single-audio prototype; more for subtitles, multi-audio and resume mapping.

## Open unknowns

- Whether the legacy Android TV playback path accepts a remote HLS strm source.
- Whether hls.js plays that source directly in Web.
- Whether a plugin can hook the stock controllers or `PlaybackInfo` through MVC options.
- Boundary re-encode cost on a NAS.
