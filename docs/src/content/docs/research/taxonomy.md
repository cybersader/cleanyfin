---
title: Tagging taxonomy & data model
description: The fixed category-plus-severity taxonomy, SponsorBlock-shaped segment schema, version calibration, and profile/bypass UX for cleanyfin.
sidebar:
  order: 6
---

*A research deep-dive from the 2026-07-21 cleanyfin research fan-out; findings, confidence ratings, and sources are preserved as captured.*

## TL;DR

Every credible content-filter (ClearPlay, VidAngel, Kids-In-Mind, Common Sense Media) converges on ~4-5 top-level categories (Language/Profanity, Sex/Nudity, Violence/Gore, Substance Use, plus a Misc/Disturbing bucket) with per-category severity. VidAngel's ~80 granular toggles prove that extreme granularity is possible but hurts usability; the right v1 is a small fixed category set (8-10) each with an ordinal severity 0-3 (mirroring ClearPlay's none/implied/explicit/graphic), plus a per-segment action (mute/skip/blur/crop/mark). The segment is the core primitive and should be modeled almost exactly like SponsorBlock's (start/end floats, category, actionType, votes, locked, videoDuration, UUID, submitter) fused with Jellyfin's native MediaSegment schema — which as of the merged PR #10530 already ships a Type enum (Intro/Outro/Recap/Preview/Commercial/Annotation) AND an Action enum (None/Skip/PromptToSkip/Mute) with StartTicks/EndTicks/ItemId. The hard problem is timestamp calibration across different encodes/cuts: solve it pragmatically with a coarse identity match (TMDB/IMDb ID + runtime bucket) plus a single user-adjustable per-file offset, and offer optional Chromaprint audio-anchor auto-alignment (the exact technique Jellyfin's Intro Skipper already uses) as an accelerator. Data stays DMCA-safe because, like SponsorBlock, cleanyfin distributes only timestamps + metadata, never copyrighted frames.

## Key Findings

### 1. Jellyfin's native MediaSegment schema (merged PR #10530, shipped in 10.10) already models both a segment Type and a separate Action — cleanyfin should extend this, not reinvent it.  ·  🟢 high

A MediaSegment entity has fields: Id (DB-generated), StartTicks, EndTicks (positions in 100ns ticks), Type, ItemId (the associated library item), StreamIndex (associated MediaStream), Action, and Comment. The MediaSegmentType enum values are Intro, Outro, Recap, Preview, Commercial, Annotation. The Action enum values are None, Skip, PromptToSkip, Mute. The MediaSegmentApi supports list/get/update/delete. Reviewers explicitly argued the server should provide segment DATA while CLIENTS decide the action ('Different users will want different actions'), which is exactly cleanyfin's per-profile model. Note: the built-in Type enum has no content-filter categories (no profanity/nudity) — those live in cleanyfin's own DB and are mapped onto Jellyfin segments (Type=Annotation or a synthetic type) at export time.

Sources: <https://github.com/jellyfin/jellyfin/pull/10530> · <https://jellyfin.org/docs/general/server/metadata/media-segments/>

### 2. SponsorBlock's segment schema is the proven crowdsource template and maps almost 1:1 onto cleanyfin's needs.  ·  🟢 high

Each SponsorBlock segment carries: segment (float array [startSec, endSec]), UUID, category (string), actionType, videoDuration (float), votes, locked, views, hidden, shadowHidden, userID, description, timeSubmitted. Categories: sponsor, selfpromo, interaction, intro, outro, preview, filler, music_offtopic, poi_highlight. actionType values: skip, mute, full (whole-video label), poi (single point of interest), chapter. Moderation rules: it takes 2 votes to change a segment's category; a score of -2 or lower hides the segment (still retained in DB); moderators can 'lock' a category or whole video once segments are timed perfectly to stop churn. The entire DB is publicly downloadable (mirror-friendly, federation-friendly).

Sources: <https://github.com/ajayyy/SponsorBlock/wiki/API-Docs/12cacf68cb8d4138d42d650dc3a284d5f448a065> · <https://wiki.sponsor.ajay.app/w/API_Docs>

### 3. SponsorBlock detects stale/mismatched timestamps via a stored videoDuration, and protects viewer privacy via a SHA-256 hash-prefix (k-anonymity) query — both directly reusable for cleanyfin's calibration and privacy.  ·  🟢 high

The videoDuration field stores the video's duration at submission time and is used to determine when a submission is out of date (i.e., the underlying media changed / was re-encoded); if the current duration no longer matches, segments are flagged as potentially stale. For privacy, clients query /api/skipSegments/:sha256HashPrefix where the prefix is the first 4-32 chars (4 recommended) of SHA256(videoID); the server returns all videos sharing that prefix so it never learns exactly which title you are watching. cleanyfin can hash its title/release identifier the same way so a node never learns which movie a household is filtering.

Sources: <https://github.com/ajayyy/SponsorBlock/wiki/API-Docs/12cacf68cb8d4138d42d650dc3a284d5f448a065> · <https://github.com/ajayyy/SponsorBlock/wiki/FAQ/4bcac26daaeac184bc37c32a8d894b59b7da5425>

### 4. The four credible filtering/rating systems converge on the same ~4-5 top-level categories, validating a small fixed taxonomy.  ·  🟢 high

ClearPlay: 4 main categories — Sex/Nudity, Violence, Language, Substance Abuse — with reported granularity of 3 Violence settings, 4 Sex&Nudity settings, 6 Language settings, and a 4-level severity ladder (none / implied / explicit / graphic); all filtering ON by default. VidAngel: 5 categories — Language (blasphemous, profane, crude, discriminatory, sexual language), Sex/Nudity/Immodesty, Violence/Blood/Gore, Alcohol or Drug Use, Miscellaneous (bodily functions, medical). Kids-In-Mind: 3 scored categories — Sex&Nudity, Violence&Gore, Language — each 0-10, plus a Substance Use list. Common Sense Media: Violence, Sex, Language, Substance (drinking/drugs/smoking), Consumerism, plus positive-messages/role-models on a 1-5 dot scale. The common core is: Language/Profanity, Sex/Nudity, Violence/Gore, Substance — with an ordinal severity per category.

Sources: <https://help.clearplay.com/docs/adjusting-filtering-settings> · <https://help.vidangel.com/hc/en-us/articles/360055496752-What-Filters-options-do-you-provide> · <https://kids-in-mind.com/about.htm> · <https://www.commonsensemedia.org/about-us/our-mission/about-our-ratings/tv>

### 5. VidAngel's ~80 granular per-word/per-scene toggles are the cautionary tale on granularity vs. usability.  ·  🟢 high

VidAngel exposes on average ~80 filters per title, letting users filter whole categories, sub-categories (e.g. only the 'graphic' portion of Violence, or leave Sexual Reference/Innuendo while removing Profanity/Blasphemy), or individual filters (specific words, specific scenes). This maximum granularity is powerful but overwhelming for setup. The design lesson for cleanyfin: default to a SMALL number of category+severity sliders (VidAngel-style whole-category/sub-category), and expose individual-segment overrides only in an 'advanced' path. Granularity should live in the DATA (every segment is individually tagged) but be COLLAPSED in the default UX to ~8-10 sliders.

Sources: <https://help.vidangel.com/hc/en-us/articles/360055496752-What-Filters-options-do-you-provide> · <https://blog.vidangel.com/customer-favorite-shows-to-filter-on-vidangel/>

### 6. The timestamp-calibration problem across encodes is real and already solved in the Jellyfin ecosystem via Chromaprint audio fingerprinting (Intro Skipper).  ·  🟢 high

Different rips/encodes have different intro lengths, black frames, and offsets, so absolute timestamps drift. Jellyfin's Intro Skipper plugin (ConfusedPolarBear / intro-skipper org) uses Chromaprint audio fingerprinting — the same AcoustID/Shazam technology, run via fpcalc/an FFmpeg extension — to create fingerprints of each episode's audio and find repeated spans, recording matched start/end timestamps. Crucially, fingerprints remain valid as long as the media file is unchanged, so calibration is a one-time cost per file. This same audio-anchor technique can align a canonical segment set to any local encode: fingerprint a short anchor region near a known segment, locate it in the local file, derive the offset.

Sources: <https://github.com/intro-skipper/intro-skipper> · <https://github.com/endrl/jellyfin-plugin-media-analyzer> · <https://github.com/intro-skipper/intro-skipper/wiki>

### 7. EDL is the lingua-franca export format and maps cleanly to cleanyfin actions, with existing Jellyfin plugins in both directions.  ·  🟢 high

The MPlayer/Kodi/Jellyfin EDL format is line-based: 'startSeconds endSeconds actionCode' where actionCode 0=cut (remove entirely), 1=mute (silence audio, keep video), 2=scene marker (seek point), 3=commercial skip (auto-skip). cleanyfin's mute→1, skip→3 (or 0), mark→2 map directly. Existing bridges: endrl/jellyfin-plugin-edl reads .edl files next to media; VTRunner/EdlToMediaSegments converts .edl into native Jellyfin MediaSegments. Exporting cleanyfin segments as per-file .edl gives instant compatibility with Kodi, Plex-adjacent tools, and offline players even without the Jellyfin plugin.

Sources: <https://kodi.wiki/view/Edit_decision_list> · <https://github.com/endrl/jellyfin-plugin-edl> · <https://github.com/VTRunner/EdlToMediaSegments>

### 8. Subtitle-anchored profanity detection is a high-value, low-cost automation to pre-seed MUTE segments — but needs guardrails.  ·  🟡 medium

Subtitle tracks (SRT/embedded) carry word-level-ish timing (per-cue start/end). A profanity word-list matched against subtitle cues yields candidate mute in/out points automatically, covering the single largest filtering category (Language) cheaply. Feasibility caveats: (1) subtitle timing is per-CUE not per-WORD, so mutes over-mute the whole line unless narrowed; (2) subtitles can themselves be misaligned vs audio — the same sub-sync/ffsubsync alignment used elsewhere should run first; (3) profanity in audio not present in subs (e.g. background, songs) is missed; (4) false positives (Scunthorpe problem) require human-in-the-loop confirmation. Recommendation: auto-generate Language/profanity mute segments as status='auto_suggested', never auto-published — a human confirms before they count as trusted.

Sources: <https://features.jellyfin.org/posts/833/enable-profanity-filter> · <https://github.com/intro-skipper/segment-editor>

### 9. Jellyfin already has an open feature request for a MUTE-capable content-filtering API, and a MUTE action is 'in the works' — cleanyfin is aligned with, not fighting, upstream direction.  ·  🟡 medium

Jellyfin feature requests 'Enable Profanity Filter' (#833) and 'API Support for Muting Media for Content Filtering Plugin Support' (#3396) are open, and community notes indicate a mute action that temporarily silences audio is in development for media segments. Media segments themselves landed in 10.10. This means cleanyfin can ride the native segment + action pipeline for skip today and mute as it matures, rather than hacking playback.

Sources: <https://features.jellyfin.org/posts/833/enable-profanity-filter> · <https://features.jellyfin.org/posts/3396/api-support-for-muting-media-for-content-filtering-plugin-support>

## Recommendations for cleanyfin

**R1. Ship a FIXED v1 taxonomy of 9 top-level categories, each with an ordinal severity 0-3, and a small enum of actions. Categories: profanity, sexual_dialogue, sex_scene, nudity, violence, gore, disturbing, substance_use, crude. Severity ladder mirrors ClearPlay: 0=none/absent, 1=mild/implied, 2=strong/explicit, 3=extreme/graphic (for profanity: 1=mild 'damn/hell', 2=strong 's-word', 3=strong 'f-word', and a separate boolean/flag 'blasphemy' since it is a values distinction not an intensity). A viewer's profile sets, per category, a max-allowed severity; any segment whose severity exceeds it is filtered. Keep the category list CLOSED in v1 — extensibility via a 'tags' free-field, not new top-level categories.**

- *Why:* All four studied systems converge on this core; an ordinal severity per fixed category gives VidAngel-like control with ~9 sliders instead of 80 toggles, keeping setup 'super easy'. A closed enum keeps the crowdsourced data consistent across federated nodes (free-form categories fragment the DB).
- *Risk / tradeoff:* Some content (e.g. religious-values objections, LGBT themes, specific phobias) does not fit 9 categories; the free-form 'tags' field partly mitigates but power users may want more axes. Blasphemy-as-flag vs. severity is a genuine modeling debate.

**R2. Model the segment as a SponsorBlock-shaped row keyed to a (title_id, release_id) pair, NOT to a raw file. Persist: uuid, title_ref (tmdb_id/imdb_id + season + episode), release_id (FK), start_ms, end_ms, category, severity, action, submitter_id, votes, status, locked, source_media_duration_ms, created_at. Separate 'release' table fingerprints an encode: runtime_ms, container, video_track_hash_optional, chapter_count, and an optional chromaprint anchor. Each local file resolves to a release (or falls back to nearest by runtime bucket) plus a per-file calibration_offset_ms. See the JSON + SQL sketch in Open Questions notes / below.**

- *Why:* Decoupling segment timing (release-relative) from the household's physical file (offset-corrected) is what makes crowdsourced timestamps portable across rips — this is the single most important architectural decision. It mirrors how SponsorBlock uses videoDuration to detect drift, generalized to multiple cuts.
- *Risk / tradeoff:* Multiple wildly different cuts (theatrical vs. extended vs. TV edit) genuinely need distinct release rows and distinct segment sets; auto-matching the wrong release silently mis-times filters, which for a family-safety tool is a serious failure mode (a missed mute is a trust-breaker). Calibration must fail safe (prefer over-filtering / prompt) when confidence is low.

**R3. Solve calibration in three escalating tiers, cheapest first: (1) coarse identity — match local file to a release by title ID + runtime within a tolerance bucket (e.g. +/-2s); (2) single user-adjustable global offset slider per file (store calibration_offset_ms), the pragmatic default; (3) OPTIONAL Chromaprint audio-anchor auto-alignment reusing Intro Skipper's fpcalc pipeline to compute the offset automatically. Do NOT require per-frame audio hashing in v1 — make it an opt-in accelerator.**

- *Why:* Keeps setup trivial for the common case (same popular encode → tier 1 just works) while giving a manual escape hatch (tier 2) and an automation path (tier 3) that reuses code already proven in the Jellyfin ecosystem. Avoids over-engineering per the maintainer's hard constraint.
- *Risk / tradeoff:* A single global offset cannot fix PROGRESSIVE drift from framerate mismatch (23.976 vs 25fps PAL), only fixed offset; those cases need the ffsubsync-style progressive alignment. Chromaprint is the slowest analysis step and adds an FFmpeg/fpcalc dependency to 'super-easy' setup — must be optional and clearly gated.

**R4. Adopt a default category→action map and let profiles override it. Defaults: profanity→mute, sexual_dialogue→mute, crude→mute (audio-only, preserve video/plot); sex_scene→skip, nudity→skip (blur/crop as an ADVANCED per-segment alternative), violence→skip, gore→skip, disturbing→skip, substance_use→none/mark by default (many households only want awareness). Store action on the segment as a DEFAULT, but resolve the ACTUAL action at playback from the profile (client-decides, per Jellyfin reviewers).**

- *Why:* Matches ClearPlay/VidAngel real behavior (language is muted to keep the film watchable; scenes are skipped), and keeping the final action client/profile-resolved matches Jellyfin's own design guidance and lets one shared segment serve households with different preferences.
- *Risk / tradeoff:* Blur/crop is technically much harder than mute/skip (needs real-time video processing / re-encode, not just a seek or volume=0) — recommend deferring blur/crop past v1 and treating those segments as 'skip' until implemented, clearly signaled so users are not surprised.

**R5. Model profiles as household → profile → filter_profile with inheritance, and a bypass request state machine. filter_profile = {per-category max_severity, per-category action_override}. Household sets a default filter_profile; each child profile inherits and may only be MORE restrictive unless an admin loosens it. Bypass flow states: REQUESTED → (APPROVED{scope: title|segment|category, expires_at} | DENIED) → EXPIRED; approvals are per-title and time-boxed. Map profiles onto Jellyfin users 1:1 where possible so existing Jellyfin auth/parental-controls are reused rather than rebuilt.**

- *Why:* Inheritance keeps configuration DRY for families (set once, override per kid); a time-boxed, per-title, approver-gated bypass is the VidAngel 'request exception' UX and prevents a blocked title from becoming a dead end. Reusing Jellyfin users avoids building a parallel identity system (easy setup).
- *Risk / tradeoff:* Jellyfin's own user/parental model is limited; a full approver workflow (notifications to a parent, approve on phone) is real product surface. Keep v1 bypass simple: admin toggles an exception in the dashboard; push-notification approval is a later nicety.

**R6. Make the data model natively export to BOTH Jellyfin MediaSegments (via the MediaSegmentApi: StartTicks/EndTicks/ItemId/Type/Action) and per-file .edl (0/1/2/3 action codes). Store times canonically as integer milliseconds internally; convert to ticks (ms*10000) for Jellyfin and to float seconds for EDL at the boundary. Map cleanyfin action mute→Jellyfin Action.Mute / EDL 1, skip→Action.Skip / EDL 3, mark→EDL 2.**

- *Why:* Dual export = instant interoperability (Jellyfin clients today, Kodi/offline via EDL) and DMCA safety (you only ever emit timestamps + metadata, never media). Integer-ms canonical storage avoids float drift and rounding bugs across the two target formats.
- *Risk / tradeoff:* Jellyfin's Mute action was still maturing at research time; if a target client ignores Action and only skips, mute segments could be dropped or wrongly skipped — export layer must degrade predictably (e.g. fall back mute→skip only with explicit user consent, since skipping muted dialogue removes plot).

**R7. Treat automation output as SUGGESTIONS, never trusted data. Subtitle+word-list profanity detection and any AI classification write segments with status='auto_suggested' and votes=0; they require one human confirmation (or N upvotes) to reach status='published'. Run subtitle-audio alignment (ffsubsync-style) BEFORE deriving mute timings, and narrow mutes toward the offending word rather than muting the whole subtitle cue.**

- *Why:* Auto-seeding covers the biggest category (Language) at near-zero cost and jump-starts a cold crowdsourced DB, but a family-safety tool cannot ship false negatives/positives unreviewed — human-in-the-loop is the quality gate that keeps trust (the whole SponsorBlock moderation ethos).
- *Risk / tradeoff:* Word-lists are language/locale-specific and miss context (reclaimed words, songs, homographs — the Scunthorpe problem); AI classification adds cost, a model dependency, and its own bias. Both risk eroding trust if promoted to 'published' without review, so the gate must be strict.

## Open Questions

- **Should severity be a single ordinal per category (0-3) or a small set of independent sub-flags (e.g. profanity: {mild, strong, sexual, blasphemy, discriminatory} as booleans)? VidAngel uses independent sub-filters; ClearPlay uses an ordinal ladder.** — *lean:* Ordinal 0-3 for the default UX (one slider per category) PLUS an optional set of boolean sub-tags on each segment for advanced filtering. This gives ClearPlay simplicity by default and VidAngel granularity when needed, without two conflicting models.
- **How should distinct CUTS of the same title (theatrical / extended / director's / TV edit) be identified and matched to a local file automatically and safely?** — *lean:* Explicit 'release' rows per cut, matched primarily by runtime bucket + optional chapter fingerprint; when match confidence is low, FAIL SAFE by prompting the user to confirm the cut rather than silently applying possibly-wrong timings.
- **What is the canonical time unit and reference frame stored in the crowdsourced DB — release-relative milliseconds, or Jellyfin ticks?** — *lean:* Integer milliseconds, release-relative, with per-file calibration_offset applied at playback. Convert to Jellyfin ticks (x10000) and EDL float-seconds only at export boundaries.
- **Should blur/crop be in v1 at all, given it requires real-time video processing unlike mute/skip?** — *lean:* No. Ship mute + skip + mark in v1; store blur/crop as valid segment actions in the schema (so data is future-proof) but render them as 'skip' with a visible notice until a processing pipeline exists.
- **For the bypass approval flow, how much workflow is v1 vs. later — inline admin toggle only, or full parent push-notification approve/deny?** — *lean:* v1 = admin-toggles-an-exception in the dashboard with an expiry; defer push-notification approval and per-profile request queues to a later release to preserve 'super easy'.
- **How aggressive should subtitle-derived auto-mutes be — mute the whole cue (safe but over-mutes) or attempt word-level narrowing (better UX, more error-prone)?** — *lean:* Default to whole-cue mute for auto_suggested segments (safe), and let human reviewers tighten to word-level on confirmation; word-level auto-narrowing is a later enhancement once forced-alignment quality is validated.

## Sources

- [Jellyfin Media Segments — official docs](https://jellyfin.org/docs/general/server/metadata/media-segments/) — Confirms media segments (10.10+) store begin/end timestamp + type; clients decide the action; segment types Intro/Outro/Recap/Preview/Commercial.
- [Jellyfin PR #10530 'Feature: Media Segments' by endrl](https://github.com/jellyfin/jellyfin/pull/10530) — Authoritative field list: MediaSegment{Id,StartTicks,EndTicks,Type,ItemId,StreamIndex,Action,Comment}; Type enum incl. Annotation; Action enum None/Skip/PromptToSkip/Mute; reviewers say clients pick the action.
- [SponsorBlock API Docs (GitHub wiki snapshot)](https://github.com/ajayyy/SponsorBlock/wiki/API-Docs/12cacf68cb8d4138d42d650dc3a284d5f448a065) — Segment fields (segment[],UUID,category,videoDuration), /segmentInfo adds votes/locked/views/hidden; sha256HashPrefix k-anonymity query; the crowdsource schema to clone.
- [SponsorBlock FAQ (GitHub wiki)](https://github.com/ajayyy/SponsorBlock/wiki/FAQ/5e261c12c16f92ee32717054e159249ae4edde55) — Moderation model: 2 votes to change category, -2 hides a segment, moderators lock categories/videos; full DB is downloadable — the federation/mirroring precedent.
- [VidAngel — What Filter options do you provide / Filter Guidelines](https://help.vidangel.com/hc/en-us/articles/360055496752-What-Filters-options-do-you-provide) — 5 categories (Language, Sex/Nudity/Immodesty, Violence/Blood/Gore, Alcohol/Drug Use, Miscellaneous), ~80 avg filters, whole-category/sub-category/individual granularity — the granularity-vs-usability datapoint.
- [ClearPlay — Adjusting Filtering Settings](https://help.clearplay.com/docs/adjusting-filtering-settings) — 4 main categories (Sex/Nudity, Violence, Language, Substance Abuse); reported 4-level severity (none/implied/explicit/graphic); all filtering on by default; skip vs mute behavior.
- [Kids-In-Mind — About our Methodology, Ratings & Reviews](https://kids-in-mind.com/about.htm) — 3 category-specific ratings (Sex&Nudity, Violence&Gore, Language) each 0-10 by quantity+context, plus Substance Use list — precedent for per-category severity scoring.
- [Common Sense Media — How We Rate and Review: TV](https://www.commonsensemedia.org/about-us/our-mission/about-our-ratings/tv) — Per-category 1-5 dot ratings across Violence, Sex, Language, Substance, Consumerism, Positive Messages/Role Models — another convergent multi-axis taxonomy.
- [Intro Skipper (Jellyfin plugin) — repo + wiki](https://github.com/intro-skipper/intro-skipper) — Chromaprint (fpcalc/AcoustID/Shazam-class) audio fingerprinting to find repeated audio spans; fingerprints stay valid while the file is unchanged — the calibration/auto-align technique to reuse.
- [endrl/jellyfin-plugin-media-analyzer](https://github.com/endrl/jellyfin-plugin-media-analyzer) — Audio fingerprinting to auto-detect intro/outro segments in Jellyfin — reference implementation for automated segment detection feeding the MediaSegment API.
- [Kodi Wiki — Edit Decision List (EDL) format](https://kodi.wiki/view/Edit_decision_list) — EDL action codes 0=cut,1=mute,2=scene marker,3=commercial skip; float-second start/end — the portable export target that maps to cleanyfin actions.
- [endrl/jellyfin-plugin-edl and VTRunner/EdlToMediaSegments](https://github.com/endrl/jellyfin-plugin-edl) — Existing bridges reading .edl beside media and converting .edl into native Jellyfin MediaSegments — proves the EDL↔MediaSegments interop path cleanyfin should target.
- [Jellyfin feature requests: Profanity Filter (#833) & Mute API for content filtering (#3396)](https://features.jellyfin.org/posts/3396/api-support-for-muting-media-for-content-filtering-plugin-support) — Open upstream demand + in-progress MUTE action, showing cleanyfin aligns with Jellyfin's roadmap rather than fighting it.
