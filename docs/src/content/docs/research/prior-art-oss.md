---
title: Prior art & OSS competitors
description: The Movie Content Filter ecosystem, SponsorBlock, TheIntroDB, and the gap no open-source project fills — cleanyfin's opening.
sidebar:
  order: 2
---

*A research deep-dive from the 2026-07-21 cleanyfin research fan-out. Lightly formatted raw findings with confidence levels and sources preserved.*

## TL;DR

The "one other project that sucks" is almost certainly the Movie Content Filter ecosystem: the original delight-im/MovieContentFilter (an open standard + crowdsourced filter site, ~157 stars, AGPL-3.0, aging PHP monolith) and its Jellyfin port jacob-willden/jellyfin-plugin-moviecontentfilter (~17 stars, GPL-3.0, "very early development," no releases, single dev, no built-in crowdsourcing/moderation). No OSS project today combines a SponsorBlock-grade crowdsourced+moderated+federated segment DB with native Jellyfin Media Segments integration, per-profile enforcement, and in-player marking — that gap is cleanyfin's opening. The pieces to assemble already exist as separate prior art: SponsorBlock (the crowdsourcing/voting/moderation/API architecture to emulate), Jellyfin's native Media Segments API (10.10+, types + skip actions, mute action in progress), intro-skipper's segment-editor (in-player "copy timestamps while you watch" marking UI), TheIntroDB (crowdsourced timestamp DB keyed by TMDB ID inside Jellyfin), and subtitle/speech auto-detection tools (cleanvid, monkeyplug). Legally, the ClearPlay/VidAngel precedent is decisive: distribute ONLY timestamps/edit-decisions, never a copy and never DRM circumvention — the Family Movie Act of 2005 protects that model, and VidAngel lost precisely because it made copies and broke DRM.

## Key Findings

### 1. The primary direct OSS competitor is the Movie Content Filter (MCF) ecosystem, which is an open STANDARD plus a crowdsourced filter site — more mature than a single repo suggests.  ·  🟢 high

Original project delight-im/MovieContentFilter (~157 stars, 9 forks, ~226 commits, AGPL-3.0) by developer Marco (delight.im). It defines an open '.mcf' filter file format (based on WebVTT), a taxonomy of categories (violence, sex/nudity, profanity, drugs, and more) each with low/medium/high severity, and skip OR mute actions. moviecontentfilter.com hosts a browsable, account-based, crowdsourced database of downloadable filters licensed CC BY-NC-SA 4.0. It explicitly positions against ClearPlay/VidAngel by offering 'an open standard and shareable content under free licenses.' Stack is a PHP monolith (PHP 45%, HTML 31%, T-SQL 9%).

Sources: <https://github.com/delight-im/MovieContentFilter> · <https://www.moviecontentfilter.com/>

### 2. The Jellyfin-specific competitor (likely the 'it sucks' one the maintainer saw) is jacob-willden/jellyfin-plugin-moviecontentfilter — an early, single-dev port of MCF with no crowdsourcing built in.  ·  🟢 high

~17 stars, 2 forks, only ~15 commits, GPL-3.0-only, C# 69% / HTML 31%. Repo self-describes as 'in very early development... many features to add (and some bugs to fix),' with 'Work in Progress' install and usage docs and NO published releases. Built on the VideoSkip browser extension and the Intro Skipper plugin. It does not alter files (choice-based skip/mute) but ships no native crowdsourced/federated database, no moderation, and no in-player marking — it consumes local .mcf files / the MCF site. Same author maintains sibling ports: script.movie.content.filter (Kodi add-on) and movie-content-filter-extension (browser extension for Netflix/Prime/Disney+/etc.).

Sources: <https://github.com/jacob-willden/jellyfin-plugin-moviecontentfilter> · <https://github.com/jacob-willden/movie-content-filter-extension> · <https://codeberg.org/jacobwillden/script.movie.content.filter>

### 3. Jellyfin ships a native Media Segments API (since 10.10) that cleanyfin should build on rather than reinvent.  ·  🟢 high

Media Segments (begin/end timestamp + type) debuted in Jellyfin 10.10. Built-in types: Intro, Outro, Commercial, Preview, Recap (plus Unknown). Segments are supplied by PLUGINS (e.g., an official Chapter Segments Provider); clients decide behavior and expose skip buttons, configured per client in playback settings. A 'mute' action (temporarily silence audio) is explicitly 'in the works' — noted in the Android TV 0.18 release (Nov 2024) — and there is an open feature request 'API Support for Muting Media for Content Filtering Plugin Support' (features.jellyfin.org #3396, posted 2025-07-22), plus an older 'Enable Profanity Filter' request (#833). This means cleanyfin can register as a segment provider; a custom 'filter' type or reuse of Commercial/mute is the integration seam.

Sources: <https://jellyfin.org/docs/general/server/metadata/media-segments/> · <https://features.jellyfin.org/posts/3396/api-support-for-muting-media-for-content-filtering-plugin-support> · <https://github.com/jellyfin/jellyfin/pull/10530>

### 4. An in-player 'mark segments while you watch' UI already exists as prior art: intro-skipper/segment-editor.  ·  🟢 high

segment-editor (intro-skipper org, TypeScript 96%, GPL-3.0, ~11 stars, early stage, no releases) is a web UI (Jellyfin plugin OR standalone) to Create/Edit/Delete segments of any type and includes 'a Player to copy timestamps while you watch.' Requires Jellyfin 10.10+ and a segments provider (e.g., Intro Skipper). This is directly the intuitive-marking flow cleanyfin wants — reusable as a design/code reference for the contributor-facing marking client.

Sources: <https://github.com/intro-skipper/segment-editor>

### 5. TheIntroDB proves the crowdsourced-timestamp-DB-inside-Jellyfin model works today (for intros/credits).  ·  🟢 high

TheIntroDB/jellyfin-plugin fetches intro, recap, credits, and preview timestamps from TheIntroDB's community API keyed by TMDB ID and exposes them as Jellyfin Media Segments so clients show skip buttons. This is the closest existing analog to cleanyfin's architecture but scoped to intros/credits, not objectionable-content categories — validating that a remote crowdsourced segment API mapped onto Jellyfin Media Segments is viable. The dominant sibling, intro-skipper/intro-skipper, instead auto-detects intros via Chromaprint audio fingerprinting (the most-installed Jellyfin plugin in 2026) — relevant as an AUTO-detection pattern, not crowdsourcing.

Sources: <https://github.com/TheIntroDB/jellyfin-plugin> · <https://github.com/intro-skipper/intro-skipper>

### 6. SponsorBlock (ajayyy) is the reference architecture to emulate for crowdsourcing, voting, moderation, and an open API.  ·  🟢 high

SponsorBlock is a free/open browser extension + open API for crowdsourced timestamped segments. Server (ajayyy/SponsorBlockServer) uses Postgres or SQLite. Key mechanics cleanyfin should copy: community submission of segments; up/down voting; category re-voting; VIP + submitter one-vote overrides for moderation; a PUBLICLY downloadable full database dump (sponsor.ajay.app/database); and a PRIVACY-PRESERVING query where the client sends only a prefix of the SHA-256 hash of the video ID so the server can't tell which exact video is being watched. Endpoints include GET /api/skipSegments (by hash prefix), POST /api/skipSegments, POST /api/voteOnSponsorTime; actionTypes include skip/mute/full/poi. DB licensed CC BY-NC-SA 4.0 with a separate commercial license — a licensing model cleanyfin should decide on deliberately.

Sources: <https://github.com/ajayyy/SponsorBlock> · <https://github.com/ajayyy/SponsorBlockServer> · <https://wiki.sponsor.ajay.app/w/API_Docs>

### 7. On Plex, mdhiggins/PlexAutoSkip is the closest analog but is server-side, in maintenance mode, and not crowdsourced.  ·  🟢 high

PlexAutoSkip is a background Python script that watches local Plex playback and auto-skips markers (intros, credits, commercials, chapters, custom markers), with options to mute/lower volume instead (via Plex setVolume API), skip-only-if-watched, ignore premieres, per-client/user filters, and marker export/audit. The maintainer notes no new major features (Plex is adding native client-side intro skip); minor fixes only. It reads Plex's own markers or user-defined custom markers — there is no shared community database. Related: sprt/skippex, lostb1t/replex.

Sources: <https://github.com/mdhiggins/PlexAutoSkip>

### 8. Kodi's EDL (Edit Decision List) is the simplest, oldest, and most portable edit-decision format — a good interop target.  ·  🟢 high

Kodi's simple time EDL format: 'startSeconds endSeconds action' per line where action is 0=cut, 1=mute, 2=scene marker, 3=commercial break; supports HH:MM:SS.sss to 3 decimals and frame-accurate specs. EDL is widely supported (Kodi, mplayer, PVR tooling). Supporting EDL import/export gives cleanyfin instant interop with Kodi and existing tooling and is trivial to implement.

Sources: <https://kodi.wiki/view/Edit_decision_list>

### 9. Subtitle- and speech-based auto-detection tools exist and can seed/assist the crowdsourced DB (partial automation prior art).  ·  🟢 high

mmguero/cleanvid (~89 stars, BSD-3-Clause, active — v1.7.1 Apr 2026, Python) reads .srt subtitles (downloads via subliminal if absent), mutes profanity segments via FFmpeg, and can EXPORT EDL files and PlexAutoSkip-compatible JSON — i.e., it already emits edit-decisions, not just cleaned files. Sibling mmguero/monkeyplug (~43 stars, BSD-3, v2.1.9 Jan 2026) does the same via speech recognition (Whisper or Vosk) for media lacking subtitles, muting or beeping. These are automation that can pre-populate profanity segments for human review — but they only cover language, not visual categories (nudity/violence), which still need human tagging.

Sources: <https://github.com/mmguero/cleanvid> · <https://github.com/mmguero/monkeyplug>

### 10. A Stremio equivalent (ameen-roayan/stremio-cleanstream) already tried the exact crowdsourced-filter model — and its choices are instructive.  ·  🟢 high

CleanStream (~16 stars, MIT, active — v1.3.1 Jan 2026, JS, Docker + PostgreSQL) is a Stremio addon that shows warnings/skip points for nudity, violence, language, drug use, fear/horror, each with low/medium/high severity. It seeded 376+ movies from the VideoSkip database, accepts community submissions via API/CLI, has up/down voting, and is MCF import/export compatible. Notably it validates: (a) MCF as an interop format, (b) Postgres + Docker as a pragmatic self-host stack, and (c) that auto-skip is hard (it currently shows warnings; auto-skip 'coming soon'). Small footprint = the niche is wide open.

Sources: <https://github.com/ameen-roayan/stremio-cleanstream>

### 11. The ClearPlay/VidAngel legal precedent dictates the safe architecture: ship edit-decisions only, never a copy, never DRM circumvention.  ·  🟢 high

The Family Movie Act of 2005 (FMA) makes filtering legal PROVIDED no fixed copy of the altered work is made and no access-control/DRM is circumvented. ClearPlay's model — real-time filtering via edit-decisions applied to the user's own legitimate copy, no copying, no decryption — was implicitly blessed by the FMA. In Disney v. VidAngel (9th Cir., decided Aug 24, 2017, No. 16-56843), VidAngel LOST because it decrypted DVDs (DMCA 1201 violation), stored infringing copies on its servers, and streamed them (infringing public performance); the FMA did not save it because the FMA does not override the reproduction/public-performance rights or the DMCA anti-circumvention ban. Direct mandate for cleanyfin: distribute only timestamps/categories/EDLs applied to media the user already possesses; never host, transcode-and-redistribute, or decrypt content.

Sources: <https://cdn.ca9.uscourts.gov/datastore/opinions/2017/08/24/16-56843.pdf> · <https://www.thompsoncoburn.com/insights/9th-circuits-vidangel-decision-vindicates-lawful-video-filtering-service/> · <https://try.clearplay.com/history-of-being-legal-full-timeline/>

### 12. Commercial reference points show the feature bar OSS has not yet met — especially seamless mute-with-context and large curated catalogs.  ·  🟡 medium

ClearPlay: subscription filtering applied to your own streams/discs, ~14 filter categories with granular sub-settings, large professionally-curated catalog. VidAngel: post-lawsuit pivoted to filtering on top of Netflix/Prime/Disney+/HBO via a browser-style overlay; huge catalog, paid. TVGuardian: a HARDWARE (and former Dish app) audio filter that reads closed captions a beat ahead of audio, checks a 150+ word/phrase dictionary (Strict/Moderate/Tolerant tiers, toggles for religious/sexual/hell-damn), mutes the sentence and shows a cleaned replacement caption — but explicitly does NOT support streaming (Netflix/Hulu/Prime). Sony's short-lived 2004 movie-filtering and 'Enjoy Movies Your Way' were early studio-adjacent efforts, now defunct. Gaps OSS lacks: (1) large, quality-curated catalogs; (2) polished sentence-level mute with substitute captions (TVGuardian's trick); (3) frictionless per-title/per-profile UX. cleanyfin's edge over all of them: free, self-hosted, federated, community-owned, and DRM-free by design.

Sources: <https://www.tvguardian.com/> · <https://alternativeto.net/software/vidangel/> · <https://try.clearplay.com/history-of-being-legal-full-timeline/>

## Recommendations for cleanyfin

**R1. Adopt the SponsorBlock server model wholesale (submission + voting + VIP/submitter-override moderation + public DB dump + hash-prefix privacy query) rather than inventing a new one, but scoped to title/version+category segments instead of YouTube video IDs.**

- *Why:* It is the proven, at-scale, open blueprint for exactly this problem (crowdsourced timestamps + abuse-resistant moderation) and is battle-tested with millions of users. Reusing its patterns saves enormous design effort and gives instant credibility.
- *Risk / tradeoff:* Its Postgres-centric server is more than a tiny project needs day one; the hash-prefix privacy scheme adds complexity. Mitigate by starting with SQLite + simple exact-ID lookups and layering privacy/Postgres later. SponsorBlock's DB is CC BY-NC-SA — decide cleanyfin's license independently.

**R2. Integrate as a Jellyfin Media Segments PROVIDER plugin (10.10+) and reuse/borrow intro-skipper/segment-editor's in-player 'copy timestamps while you watch' UI for the contributor marking flow.**

- *Why:* Media Segments is native infrastructure that already renders skip buttons and is getting a mute action; building on it means cleanyfin inherits client support instead of forking clients. segment-editor already solved the in-player marking UX cleanyfin needs.
- *Risk / tradeoff:* Media Segments' mute action is still 'in progress,' and current segment types don't include a 'content-filter' type — you may have to map to Commercial/Unknown or push a PR upstream. Client support for mute varies. Track feature request #3396.

**R3. Make MCF (WebVTT-based .mcf) and Kodi EDL first-class import/export formats, and seed the DB from open sources (VideoSkip's ~376-title set, MCF site's CC BY-NC-SA filters, cleanvid/monkeyplug auto-generated profanity segments).**

- *Why:* Interop with MCF/EDL gives instant compatibility with Kodi, PlexAutoSkip, Stremio CleanStream, and existing filter libraries, and lets you launch with non-empty data — the cold-start problem is the biggest risk for any crowdsourced DB.
- *Risk / tradeoff:* MCF-site filters are CC BY-NC-SA (non-commercial, share-alike) — importing them can constrain cleanyfin's own DB license and commercial reuse. Segment matching across releases/cuts (theatrical vs extended, different framerates/offsets) is genuinely hard; store per-version keys and per-title offsets.

**R4. Design the DB from day one around a stable content key + explicit version/edition + timing anchor, keyed off TMDB/IMDb IDs (as TheIntroDB does) with a per-file offset field.**

- *Why:* The single biggest reason MCF-style local-file filters 'suck' in practice is fragile matching to the exact video; robust versioning + an offset anchor is what makes crowdsourced segments actually line up on a stranger's copy.
- *Risk / tradeoff:* Perfect matching is unsolvable in general (recuts, PAL speedup, ad-inserted rips). Accept 'good enough + user nudge' and let clients apply a manual offset; expose confidence/votes so bad matches self-correct.

**R5. Position and license cleanyfin explicitly on the ClearPlay/Family-Movie-Act side of the line: distribute ONLY timestamps/categories/edit-decisions applied to media the user already owns; never host, decrypt, re-encode-and-redistribute, or bundle any copyrighted frames/audio.**

- *Why:* This is the exact distinction that won for ClearPlay and sank VidAngel in the 9th Circuit. Staying strictly on metadata keeps cleanyfin in SponsorBlock's safe, long-lived legal posture and satisfies the maintainer's DMCA/patent constraint.
- *Risk / tradeoff:* Contributors might try to attach clips/screenshots 'for reference' — that would import infringement risk. Enforce a metadata-only submission schema (no media payloads) and document the legal rationale in CONTRIBUTING.

**R6. Differentiate on the four things every existing OSS competitor lacks together: (1) real crowdsourcing+moderation, (2) federation/self-host, (3) native per-profile Jellyfin enforcement + request-bypass, (4) frictionless in-player marking. Keep the stack boring: single Docker container, SQLite default, Postgres optional.**

- *Why:* No current project (MCF plugin, PlexAutoSkip, CleanStream, cleanvid) offers all four; that combination is cleanyfin's whole reason to exist. A one-command Docker + SQLite default directly satisfies the 'super easy to self-host' hard constraint.
- *Risk / tradeoff:* Federation is the hardest of the four and easy to over-engineer — defer true node-to-node federation to v2; ship a single-node self-host + optional read-through to a public community instance first (subsidiarity without premature distributed-systems complexity).

## Open Questions

- **Is the 'sucks' competitor the maintainer saw the Jellyfin MCF plugin (jacob-willden) or the original MCF site/standard (delight-im)? They differ a lot in maturity.** — *lean:* Almost certainly jacob-willden/jellyfin-plugin-moviecontentfilter — it's the only Jellyfin-specific content-filter plugin, is in 'very early development' with no releases, and matches 'definitely sucks.' But cleanyfin should treat the broader delight-im MCF standard as the real prior art to interoperate with, not just the weak plugin.
- **Should cleanyfin adopt/extend the existing MCF (.mcf/WebVTT) format and category taxonomy, or define its own segment schema?** — *lean:* Extend MCF as the interchange format (for interop and cold-start seeding) but use a richer native DB schema (per-version keys, votes, offsets, provenance). Do not fork the taxonomy gratuitously — reuse MCF/CleanStream categories (violence, nudity/sex, profanity, drugs, fear/horror) + low/med/high.
- **What license should the crowdsourced segment database use, given MCF/SponsorBlock both use CC BY-NC-SA 4.0?** — *lean:* Lean CC0 or CC BY 4.0 for the DB to maximize reuse and federation and avoid the NC/share-alike friction that limits SponsorBlock/MCF data — but this conflicts with importing CC BY-NC-SA seed data, so decide before seeding. Code can be AGPL/GPL like the incumbents.
- **Does registering a custom content-filter segment type require upstream Jellyfin changes, or can cleanyfin ride existing types (Commercial/Unknown) + the forthcoming mute action?** — *lean:* Ship v1 mapping to existing types + skip, and file/track an upstream request (aligns with feature request #3396) for a dedicated filter type and mute action so per-profile mute works cleanly.
- **How should segment-to-file matching handle recuts, framerate/PAL-speedup, and ad-inserted rips that shift timing?** — *lean:* Key by TMDB/IMDb + edition, store a user-adjustable global offset per file (TheIntroDB/PlexAutoSkip pattern), and rely on votes/confidence to surface bad matches rather than attempting automated frame-fingerprint alignment in v1.

## Sources

- [delight-im/MovieContentFilter (GitHub)](https://github.com/delight-im/MovieContentFilter) — The original open MCF standard + crowdsourced filter platform; ~157 stars, AGPL-3.0, categories+severity, skip/mute, PHP monolith. The core prior art to interoperate with.
- [moviecontentfilter.com](https://www.moviecontentfilter.com/) — Live crowdsourced filter site for the MCF project; downloadable filters licensed CC BY-NC-SA 4.0; 'choice not censorship' framing.
- [jacob-willden/jellyfin-plugin-moviecontentfilter (GitHub)](https://github.com/jacob-willden/jellyfin-plugin-moviecontentfilter) — The only Jellyfin-specific content-filter plugin; ~17 stars, GPL-3.0, 'very early development,' no releases — the likely 'it sucks' competitor and cleanyfin's opening.
- [jacob-willden/movie-content-filter-extension (GitHub)](https://github.com/jacob-willden/movie-content-filter-extension) — Browser-extension MCF port for Netflix/Prime/Disney+/HBO/etc.; based on VideoSkip; shows the multi-client MCF family.
- [Kodi add-on: script.movie.content.filter (Codeberg)](https://codeberg.org/jacobwillden/script.movie.content.filter) — Kodi implementation of MCF using local .mcf files; GPL-3.0; demonstrates the local-file (non-crowdsourced) weakness.
- [Jellyfin Media Segments docs](https://jellyfin.org/docs/general/server/metadata/media-segments/) — Native segment infrastructure (10.10+): types Intro/Outro/Commercial/Preview/Recap, plugin-provided, client skip actions. The integration seam for cleanyfin.
- [Jellyfin Feature Request #3396: Muting Media for Content Filtering](https://features.jellyfin.org/posts/3396/api-support-for-muting-media-for-content-filtering-plugin-support) — Open request (2025-07-22) for a mute action/API specifically to support content-filtering plugins — directly relevant, track/support upstream.
- [intro-skipper/segment-editor (GitHub)](https://github.com/intro-skipper/segment-editor) — Web UI to create/edit Jellyfin segments with an in-player 'copy timestamps while you watch' player — prior art for cleanyfin's marking flow. GPL-3.0, early stage.
- [TheIntroDB/jellyfin-plugin (GitHub)](https://github.com/TheIntroDB/jellyfin-plugin) — Crowdsourced intro/recap/credits timestamp DB keyed by TMDB ID, surfaced as Jellyfin Media Segments — closest working analog to cleanyfin's remote-DB architecture.
- [intro-skipper/intro-skipper (GitHub)](https://github.com/intro-skipper/intro-skipper) — Most-installed Jellyfin plugin; auto-detects intros/credits via Chromaprint audio fingerprinting — the AUTO-detection pattern (vs crowdsourcing).
- [ajayyy/SponsorBlock + SponsorBlockServer (GitHub)](https://github.com/ajayyy/SponsorBlockServer) — The reference architecture: Postgres/SQLite, submission+voting+VIP moderation, public DB dump, open API; the model to emulate.
- [SponsorBlock API Docs](https://wiki.sponsor.ajay.app/w/API_Docs) — Endpoint reference (skipSegments by hash prefix, voteOnSponsorTime), actionTypes (skip/mute/full/poi), and the privacy-preserving hash-prefix query design.
- [mdhiggins/PlexAutoSkip (GitHub)](https://github.com/mdhiggins/PlexAutoSkip) — Plex analog: server-side Python auto-skip/mute of markers; in maintenance mode; no shared DB — shows the single-instance, non-crowdsourced limitation.
- [mmguero/cleanvid (GitHub)](https://github.com/mmguero/cleanvid) — Subtitle(SRT)-based profanity muting via FFmpeg; ~89 stars, BSD-3, active (v1.7.1, Apr 2026); exports EDL + PlexAutoSkip JSON — partial-automation prior art and interop target.
- [mmguero/monkeyplug (GitHub)](https://github.com/mmguero/monkeyplug) — Speech-recognition (Whisper/Vosk) profanity mute/beep for media lacking subtitles; ~43 stars, BSD-3 — automation that can seed profanity segments.
- [ameen-roayan/stremio-cleanstream (GitHub)](https://github.com/ameen-roayan/stremio-cleanstream) — Stremio content-filter addon; MCF-compatible, community voting, Postgres+Docker, MIT; seeded from VideoSkip. Validates the crowdsourced-filter model and reveals auto-skip difficulty.
- [Kodi Wiki: Edit Decision List (EDL)](https://kodi.wiki/view/Edit_decision_list) — The simple, portable edit-decision format (0=cut,1=mute,2=scene,3=commercial); trivial import/export interop target.
- [9th Circuit opinion, Disney v. VidAngel (No. 16-56843, Aug 24 2017)](https://cdn.ca9.uscourts.gov/datastore/opinions/2017/08/24/16-56843.pdf) — Primary court record: VidAngel lost for making infringing copies + DMCA circumvention; Family Movie Act does not override reproduction/public-performance rights. Defines the safe design boundary.
- [Thompson Coburn: 9th Circuit's VidAngel decision (legal analysis)](https://www.thompsoncoburn.com/insights/9th-circuits-vidangel-decision-vindicates-lawful-video-filtering-service/) — Plain-English analysis contrasting ClearPlay (legal, edit-decisions only) vs VidAngel (illegal, copies+DRM).
- [TVGuardian (official site)](https://www.tvguardian.com/) — Commercial reference: closed-caption profanity filter that mutes sentences + shows cleaned captions from a 150+ word dictionary; no streaming support — feature bar for language filtering.
- [VidAngel Alternatives (AlternativeTo)](https://alternativeto.net/software/vidangel/) — Landscape of commercial/free filtering alternatives for feature-parity reference.
