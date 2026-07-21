# Research Sources

> Curated primary/authoritative sources behind the locked decisions. Real content, not a pointer stub. Full per-finding citations live in the six deep-dives under [`../knowledge-base/01-working/`](../knowledge-base/01-working/); this is the shortlist worth keeping one click away. Gathered in the 2026-07-21 research fan-out.

## Legal foundation (the metadata-only keystone — R01)

- **[Family Movie Act / PL 109-9 (copyright.gov)](https://www.copyright.gov/legislation/pl109-9.html)** — the operative statute (17 U.S.C. §110(11)): real-time "making imperceptible" of an *authorized* copy, no *fixed* edited copy. The four load-bearing conditions cleanyfin is designed around.
- **[9th Circuit opinion, Disney v. VidAngel, No. 16-56843 (PDF)](https://cdn.ca9.uscourts.gov/datastore/opinions/2017/08/24/16-56843.pdf)** — the primary court record: VidAngel lost for DRM circumvention (DMCA §1201) + making/streaming copies; the FMA did *not* save it.
- **[Thompson Coburn — analysis of the VidAngel decision](https://www.thompsoncoburn.com/insights/9th-circuits-vidangel-decision-vindicates-lawful-video-filtering-service/)** — plain-English ClearPlay (legal, edit-decisions only) vs VidAngel (illegal, copies + DRM).
- **[U.S. Copyright Office §1201 (anti-circumvention)](https://www.copyright.gov/1201/)** — why never decrypting anything is the bright line.
- **[Feist v. Rural Telephone](https://en.wikipedia.org/wiki/Feist_Publications,_Inc.,_v._Rural_Telephone_Service_Co.)** — facts/timestamps are thin-to-no copyright (why SponsorBlock's dataset is defensible).

## The data model to emulate (SponsorBlock — R03, R08)

- **[SponsorBlockServer (GitHub)](https://github.com/ajayyy/SponsorBlockServer)** — the exact stack analogy: TS/Node, Postgres *or* SQLite, AGPL-3.0 code + CC BY-NC-SA data, Docker.
- **[SponsorBlock API Docs (wiki)](https://wiki.sponsor.ajay.app/w/API_Docs)** — segment fields, actionTypes (skip/mute/full/poi), submit/vote endpoints, `videoDuration` staleness detection, 429/409 handling.
- **[SponsorBlock K-Anonymity (wiki)](https://github.com/ajayyy/SponsorBlock/wiki/K-Anonymity)** — hash-prefix queries so the server never learns which title you're watching.
- **[SponsorBlock database dumps](https://sponsor.ajay.app/database)** + **[sb-mirror](https://github.com/sylv/sb-mirror)** — the public-dump + incremental-mirror pattern = cleanyfin's v1 "federation."

## Jellyfin integration (R02, R07)

- **[Media Segments — Jellyfin docs](https://jellyfin.org/docs/general/server/metadata/media-segments/)** — the native feature (10.10+): types, plugin-provided, client skip actions.
- **[Media Segments proposal — jellyfin-meta #30](https://github.com/jellyfin/jellyfin-meta/discussions/30)** & **[PR #10530](https://github.com/jellyfin/jellyfin/pull/10530)** — the segment/action enums (Action: None/Skip/PromptToSkip/**Mute**) and the "clients decide the action" design; mute deferred.
- **[Feature request #3396 — Mute API for content filtering](https://features.jellyfin.org/posts/3396/api-support-for-muting-media-for-content-filtering-plugin-support)** — the upstream signal to track for native mute.
- **[Intro Skipper](https://github.com/intro-skipper/intro-skipper)** (reference provider + Chromaprint calibration) · **[segment-editor](https://github.com/intro-skipper/segment-editor)** (in-player "copy timestamps while you watch") · **[TheIntroDB plugin](https://github.com/TheIntroDB/jellyfin-plugin)** (crowdsourced remote segment DB, keyed by TMDB) · **[jellyfin-plugin-template](https://github.com/jellyfin/jellyfin-plugin-template)** (the C# scaffold).

## Prior art & the competitor (R11 · see `05-EXISTING-WORK`)

- **[jacob-willden/jellyfin-plugin-moviecontentfilter](https://github.com/jacob-willden/jellyfin-plugin-moviecontentfilter)** — the direct (weak) competitor.
- **[delight-im/MovieContentFilter](https://github.com/delight-im/MovieContentFilter)** + **[moviecontentfilter.com](https://www.moviecontentfilter.com/)** — the open `.mcf` standard to interoperate with.
- **[Kodi EDL format](https://kodi.wiki/view/Edit_decision_list)** (0=cut/1=mute/2=scene/3=commercial) · **[endrl/jellyfin-plugin-edl](https://github.com/endrl/jellyfin-plugin-edl)** — the portable edit-decision interchange + the real-mute path for Kodi/mpv.
- **[cleanvid](https://github.com/mmguero/cleanvid)** / **[monkeyplug](https://github.com/mmguero/monkeyplug)** — subtitle/speech profanity automation (seed candidates, R10).
- **[PlexAutoSkip](https://github.com/mdhiggins/PlexAutoSkip)** · **[stremio-cleanstream](https://github.com/ameen-roayan/stremio-cleanstream)** — adjacent-ecosystem analogs.

## Version matching & tech stack (R04, R05, and the stack lean)

- **[OpenSubtitles moviehash / OSHash](https://opensubtitles.github.io/oshash/)** + **[implementation](https://github.com/opensubtitlescli/moviehash)** — the fingerprint (filesize + first/last 64KB) that maps a segment set to the *right* rip.
- **[Chromaprint / AcoustID](https://github.com/acoustid/chromaprint)** — the opt-in v2 cross-rip audio-anchor alignment (same tech Intro Skipper uses).
- **[modernc.org/sqlite](https://pkg.go.dev/modernc.org/sqlite)** — CGo-free pure-Go SQLite → the single static binary story.
- **[Litestream](https://litestream.io/how-it-works/)** — continuous SQLite replication for disaster recovery without a DB server.
- **[Kevinjil/jellyfin-plugin-repo-action](https://github.com/Kevinjil/jellyfin-plugin-repo-action)** — auto-generate the plugin `manifest.json` repo in CI.

## Taxonomy reference systems (R05)

- **[ClearPlay filtering settings](https://help.clearplay.com/docs/adjusting-filtering-settings)** · **[VidAngel filter options](https://help.vidangel.com/hc/en-us/articles/360055496752-What-Filters-options-do-you-provide)** · **[Kids-In-Mind methodology](https://kids-in-mind.com/about.htm)** · **[Common Sense Media ratings](https://www.commonsensemedia.org/about-us/our-mission/about-our-ratings/tv)** — the four systems that converge on ~4–5 categories × severity.
