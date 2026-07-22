# cleanyfin — Current Focus

> Update when direction changes, milestones complete, or priorities shift.

**Current:** **Phase 1 nearly closed — spikes done, docs live, one gate left.** On 2026-07-21: (1) six-dimension research fan-out → `knowledge-base/01-working/` + the `.claude/` orientation layer; (2) **feasibility spikes A/B/C resolved** against Jellyfin 10.11 source (enforcement → R13, write-path → R14, client-support → R07 updated); (3) **docs site stood up** (`docs/`, Astro + Starlight, 23 pages, build + 5 smoke tests green). Still no product code — deliberately. **All Phase-1 gates are now cleared: spikes done + data license decided (R15 — CC0 data + AGPL-3.0 code). Production code (the thin vertical slice, Phase 3) is unblocked.** Identity + v1 architecture locked (below).

Last updated: 2026-07-21

## Locked This Cycle (do not relitigate without new evidence)

- **Identity:** open-source, self-hosted **content-filtering layer for Jellyfin** on a **federated, crowdsourced segment DB**. VidAngel experience, SponsorBlock data. Category word: "layer/filter." (see `PROJECT_CONTEXT`)
- **Legal keystone:** metadata-only, never media, never DRM circumvention — the Family-Movie-Act/ClearPlay side of the line. (R01, `01-PROBLEM`, `legal-and-ip-landscape.md`)
- **Architecture:** thin C# `IMediaSegmentProvider` plugin + small self-hostable API server (SponsorBlock-clone) + companion marking PWA. Build on Jellyfin Media Segments; don't fork clients. (R02, `21-ARCHITECTURE`)
- **Stack lean:** Go single static binary embedding the PWA (`modernc.org/sqlite`, `embed.FS`); SQLite-WAL default, optional Litestream; one `docker compose up` + systemd path. Postgres only at SponsorBlock scale. (Hard Constraint #2; `tech-stack-and-devops.md`) — *lean, not a locked decision, pending the language-fluency call (Q1 in `40-QUESTIONS-OPEN`).*
- **Federation v1:** SponsorBlock model — one open hub + public dumps + trivial read-only mirrors (sb-mirror pattern). Subsidiarity via subscribable **curator profiles** in one open dataset. DEFER ActivityPub/nostr/matrix/shared-DB CRDTs; design the signed-Git-bundle upgrade path now. (R03, `federation-architecture.md`)
- **Version matching:** key segments to `(title_id + release fingerprint)` = OpenSubtitles moviehash + exact duration; per-file `calibration_offset`; Chromaprint audio-anchor auto-align is opt-in v2; **fail safe** when match confidence is low. (R04, `22-DATA-MODEL`)
- **Taxonomy:** fixed 9 categories × severity 0–3 + action enum (mute/skip/mark; blur/crop schema-reserved, rendered as skip in v1). Default category→action map; profile resolves the actual action. (R05, R06, `03-CONCEPTS`, `tagging-taxonomy-and-data-model.md`)
- **MVP filtering behavior:** **SKIP-only**, fleet = Web + Android TV + Roku + Kodi (native), webOS partial, Swiftfin/iOS the gap; native mute still doesn't exist anywhere, so **EDL (emitted from our own data)** is the real-mute path for Kodi/mpv. Honest about the mute gap. (R07 — updated by Spike C, `spike-c-client-support.md`)
- **Enforcement:** no Jellyfin seam carries per-user context (verified) → default global provider + honest opt-in; **optional cleanyfin reverse-proxy** filters the `/MediaSegments` response per user for real per-profile enforcement on the stable public HTTP contract. (R13, `spike-a-enforcement.md`)
- **Segment write path:** PWA → cleanyfin's Go API (source of truth); plugin materializes at scan + hosts its own thin write controller; not Intro Skipper's route. Shipped `MediaSegmentDto` = `Id, ItemId, Type, StartTicks, EndTicks` only. (R14, `spike-b-segment-write-api.md`)
- **Identity/moderation:** account-free pseudonymous submitter IDs + moderation queue (auto-hide at vote ≤ −2, shadowban, curator-lock). (R08)
- **Automation:** subtitle/word-list profanity + AI classification produce `status='auto_suggested'` only; human-in-the-loop gate before `published`. (R10)

## The Competitor (the opening)

`jacob-willden/jellyfin-plugin-moviecontentfilter` — the only Jellyfin-specific content-filter plugin, "very early development," single dev, no releases, **no crowdsourcing / moderation / federation / in-player marking.** That's almost certainly the "it definitely sucks" project. The broader `delight-im/MovieContentFilter` **standard** (.mcf/WebVTT + taxonomy) is real prior art to *interoperate* with, not dismiss. cleanyfin's opening = the four things no OSS project offers together: real crowdsourcing+moderation, federation/self-host, native per-profile Jellyfin enforcement + request-bypass, and frictionless in-player marking. (`05-EXISTING-WORK`, `prior-art-and-oss-competitors.md`)

## What's Next (see `20-ROADMAP`)

1. ~~Two feasibility spikes~~ **DONE 2026-07-21** (A→R13 enforcement, B→R14 write-path, C→R07 client-support; verified vs 10.11 source).
2. ~~Stand up the docs site~~ **DONE 2026-07-21** (`docs/` Astro+Starlight, 23 pages, build + smoke green). *Remaining:* a GitHub Pages deploy workflow once the repo is pushed.
3. ~~Decide the data license~~ **DECIDED 2026-07-21 → R15: CC0-1.0 (data) + AGPL-3.0-or-later (code).** `LICENSE` + `DATA-LICENSE` written. Seeding via auto-generation + original contributions (no BY-NC-SA ingest).
4. **Phase 3 IN PROGRESS.** Slice 1 (Go API + `docker compose up`) ✅ merged to main (PR #1). **Slice 2 ✅ DONE 2026-07-22** on branch `feat/slice-2-clients`: the C# `IMediaSegmentProvider` plugin (`plugin/`, `dotnet build` clean vs Jellyfin.Controller 10.11.11) + the marking PWA (`pwa/`, `bun run build` clean) + a CORS middleware on the API so they interoperate; CI gates `plugin-ci.yml` added. All three components now compile/build/test. **Slice 3 ✅ DONE 2026-07-22:** real **moviehash** fingerprint (R04) — the plugin computes the OpenSubtitles moviehash (`osh:`…) per file + exposes `GET /Cleanyfin/Fingerprint` so the PWA resolves the same fp; algorithm verified against hand-computed vectors; plugin + PWA rebuilt clean. **Next:** real end-to-end validation on a live Jellyfin 10.11 server; cross-rip **calibration offset** (differently-encoded copies); the write controller (R14) + response-filtering proxy (R13); hash-prefix privacy query (R08).

## Deliberately NOT Doing Right Now

- Sprawling code before the thin vertical slice is scoped — the gates (spikes + license) are done, so Phase 3 can start deliberately, one slice at a time.
- Building ActivityPub / nostr / matrix / shared-DB CRDT federation (mirrors + dumps are v1).
- Blur/crop video processing (schema-reserved; renders as skip in v1).
- Native mute (upstream-dependent; ship skip + EDL-mute).
- Push-notification bypass approval workflow (v1 = admin dashboard toggle with expiry).

## Pointers

- **Research deep-dives:** `knowledge-base/01-working/*.md` (6 files)
- **Repo:** https://github.com/cybersader/cleanyfin
- **Reference implementations to clone:** SponsorBlockServer, Intro Skipper, jellyfin-plugin-template, endrl/jellyfin-plugin-edl, intro-skipper/segment-editor
