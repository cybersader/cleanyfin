# Cleanyfin — Upkeep ledger

**Living document · started 2026-11-21.** Cleanyfin does not own a media server or a player. It rides on other people's software, and that is a deliberate trade: less to build, more to keep re-checking. This page is the honest list of what we ride on, what breaks when it moves, and what work that creates. Anyone deciding whether to run, contribute to or depend on Cleanyfin should read this before the roadmap.

**Rule:** a change that adds something Cleanyfin depends on, or leans on behaviour the owner of that thing has not promised, adds or updates a row here in the same change. No row, no merge. This is item 7 of the [delivery checklist](13-DELIVERY-PATTERN.md).

## What we ride on

Firmness: **promised** (a documented interface), **observed** (works today, nobody promised it), **ours** (we control it).

| We depend on | Whose it is | Firmness | What breaks if it changes | Upkeep it creates |
|---|---|---|---|---|
| Jellyfin plugin interfaces (plugin loading, segment provider, controllers, DI registration) | Jellyfin | promised, per server version | Plugin fails to load or build | Rebuild and retest for each Jellyfin release line. The plugin targets 10.11.11; current Jellyfin is 12.4 and is **not yet verified**. |
| Jellyfin behaviour the filtered item relies on: a request filter on PlaybackInfo for generated items only; a startup filter that hands a ticketed request to Jellyfin's own static-file route; how `.strm` items are probed and refreshed | Jellyfin | **observed, not promised** | Filtered items stop playing or stop appearing. By design they then offer nothing; they do not fall back to the original | Rerun `tools/playback-lab/check-plugin.py` (all cases) on every Jellyfin version before saying it is supported. Expect to rework this layer on major releases. It is the least solid layer. |
| Jellyfin's library access model | Jellyfin | promised, but with a default that surprises | A filtered shelf is an ordinary library. Any account set to "access all libraries", including accounts created later, can see the shelf and its titles. They cannot play from it unless enrolled | Households that want a shelf hidden must set each account's library list by hand. Cleanyfin does not hide it for them. |
| Native shelf registration, targeted collection refresh, per-item user-data keys, and ancillary item routes | Jellyfin | **observed on 10.11.11** | A shelf can disappear, duplicate items, inherit the wrong runtime or share a bookmark; a changed image/download route can escape suppression | Run `shelf-isolation` after every host upgrade: two profiles, filtered resume and completion, changed timeline, rescan/restart, uninstall, and cleanup. Never compensate by writing the original bookmark. |
| Pinned Linux file mounts, `statx` mount/inode/change time and the image’s local ffprobe | Linux; Jellyfin FFmpeg image | **observed in the disposable lab** | Stale bytes or container metadata could be mistaken for the reviewed edition | Recheck same-size/same-duration replacement, preserved mtime, physical streams and source clocks on each kernel/image change. Refuse mutable sources and unverified mounts. |
| Jellyfin's default logging | Jellyfin | observed | At logout Jellyfin writes the user's login token to its own log at default level | Recommend raising `SessionManager` logging to Warning. Cleanyfin's own tickets were not found in logs in lab runs; recheck when logging changes. |
| A player that honours a cut list. Today: Wholphin 1.1.2 with its mpv backend | Wholphin's maintainer; mpv | **observed** | The app could change how it chooses direct play, drop its mpv backend, or change mpv options. Filtering on that app then stops, or worse, stops being exact | Every app version and playback mode is measured before it is listed as supported, and re-measured when it updates. Evidence so far is one Google TV emulator: no physical device, no sound measurement, and a recording too slow to prove no frame is lost at a cut. |
| mpv's cut-list format and its behaviour at a join | mpv | promised format, observed behaviour | Desktop mpv alters about 9 ms of kept sound just after a cut. mpv refuses a remote cut list that points at local files, and that check must stay on | Re-measure joins when mpv changes. Never ask users to relax mpv's safety settings. |
| Stock Jellyfin apps (Web, Android TV, Roku, Swiftfin and others) | Jellyfin | n/a | They do not act on Cleanyfin's marks and their own skipping reacts late | Nothing to maintain, but the docs must keep saying **unsupported** until an app is measured. Getting support upstream means a human writing to those projects; Jellyfin does not accept machine-written issues or comments. |
| The plan, reviewed-marks and viewer-settings formats (`delivery/schema/*.json`) | Cleanyfin | **ours** | Other layers and any outside tool reading these | Versioned. A change in meaning is a new version, never a quiet edit. Contract tests run on every change. |
| Reviewed marks tied to one exact file | The household | ours, and manual | A re-encode, a different edition or a replaced file no longer matches, so its filtered item disappears | A person re-reviews marks for each new copy. There is no automatic carry-over between different cuts of a movie, on purpose. |
| The shared marks database feeding reviewed marks | Cleanyfin | **not built** | n/a | Reviewed marks are placed by hand today. |
| The test lab: a pinned Jellyfin image, Docker, an Android TV emulator | Jellyfin, Docker, Google | observed | Lab checks cannot run | Bump the pinned image deliberately and record the new evidence. |
| The legal reading behind "no edited copy is ever stored" | U.S. statute and cases | research only | A server-side cutting feature could fall outside it | Cleanyfin stores and streams no edited media. Anything that would needs a lawyer first. The reading is U.S. only. See the [research note](../knowledge-base/01-working/client-ecosystem-2026-11-21/temporary-copies-and-family-movie-act.md); it is not legal advice. |

## Work each outside event creates

| When this happens | Someone has to |
|---|---|
| Jellyfin ships a new version | Rebuild the plugin, rerun every lab case, update the supported-version statement. Until then that version is unsupported. |
| A supported player app updates | Re-measure it on the test clip. Until then that app version is unsupported. |
| A movie file is replaced or re-encoded | Re-review its marks against the new file. |
| A mark or a viewer's settings change | Nothing by hand: outstanding links are cancelled and the plan is rebuilt. |
| A household adds a Jellyfin account | Check that account's library list if the filtered shelf should be hidden from it. |
| Cleanyfin adds a player, a kind of edit, or a new dependency | Add or update a row above, with evidence. |

## What this means in plain terms

Cleanyfin stays small because it borrows the server and the player. The price is that it can be broken by releases it does not control, and it answers that by refusing to offer filtering it has not re-proven, not by guessing. Expect a filtered item to be unavailable for a while after a Jellyfin or player update. That is the system working as intended, and it is real ongoing work for whoever maintains the project.
