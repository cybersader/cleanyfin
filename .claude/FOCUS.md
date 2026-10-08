# cleanyfin — Current Focus

> Update when direction changes, milestones complete, or priorities shift.

**Current correction (2026-10-08):** API/plugin/PWA components exist; production filtering remains UNVERIFIED. The following July chronology is historical, not a current release-readiness verdict. Native Unknown is experimental, metadata selection is not enforcement, and complete installation/recovery remain unproved. [Reviewed proposal](../knowledge-base/01-working/long-term-architecture-2026-10-07/04-architecture-proposal.md) and [E1–E12 roadmap](../knowledge-base/01-working/long-term-architecture-2026-10-07/05-roadmap-and-experiments.md) remain PROPOSED except for the sequencing decision below; experiments were not run.

**Owner decision (2026-10-08):** dependable filtering on explicitly supported cooperative clients is the first-release target. Server-side enforcement/deliberate-bypass resistance stays on the LONGER-TERM roadmap, not rejected and not a first-release prerequisite. Owner response: “Yep but keep server-side enforcement on the longer-term roadmap”. This approves sequencing only, not the broader architecture, any technique, client/action choice, failure policy, exposure tolerance, timing/SLO targets, public launch or legal clearance. Actual playback correctness remains gated on experiments. Trust/privacy, recovery and maintenance budgets, and enforcement design remain open.

**Historical July state (superseded where corrected below):** On 2026-07-21: (1) six-dimension research fan-out → `knowledge-base/01-working/` + the `.claude/` orientation layer; (2) **feasibility spikes A/B/C resolved** against Jellyfin 10.11 source (enforcement → R13, write-path → R14, client-support → R07 updated); (3) **docs site stood up** (`docs/`, Astro + Starlight, 23 pages, build + 5 smoke tests green). Still no product code — deliberately. **All Phase-1 gates are now cleared: spikes done + data license decided (R15 — CC0 data + AGPL-3.0 code). Production code (the thin vertical slice, Phase 3) is unblocked.** Identity + v1 architecture locked (below).

Last updated: 2026-10-08 (documentation correction only; no new runtime validation)

## Recorded July direction — factual corrections below supersede unsupported claims

R15 remains the recorded code/data-license choice. Metadata-only distribution does not establish legal, patent, trademark or import clearance. One-artifact embedded UI, systemd, curator feeds, private household policy, verified recovery and release completeness remain PROPOSED/UNVERIFIED. Current Compose deploys the API only; database escalation requires measured bottlenecks, not SponsorBlock-sized row counts.

- **Identity:** open-source, self-hosted **content-filtering layer for Jellyfin** on a **federated, crowdsourced segment DB**. VidAngel experience, SponsorBlock data. Category word: "layer/filter." (see `PROJECT_CONTEXT`)
- **Legal keystone:** metadata-only, never media, never DRM circumvention — the Family-Movie-Act/ClearPlay side of the line. (R01, `01-PROBLEM`, `legal-and-ip-landscape.md`)
- **Architecture:** thin C# `IMediaSegmentProvider` plugin + small self-hostable API server (SponsorBlock-clone) + companion marking PWA. Build on Jellyfin Media Segments; don't fork clients. (R02, `21-ARCHITECTURE`)
- **Stack lean:** Go single static binary embedding the PWA (`modernc.org/sqlite`, `embed.FS`); SQLite-WAL default, optional Litestream; one `docker compose up` + systemd path. Postgres only at SponsorBlock scale. (Hard Constraint #2; `tech-stack-and-devops.md`) — *lean, not a locked decision, pending the language-fluency call (Q1 in `40-QUESTIONS-OPEN`).*
- **Federation v1:** SponsorBlock model — one open hub + public dumps + trivial read-only mirrors (sb-mirror pattern). Subsidiarity via subscribable **curator profiles** in one open dataset. DEFER ActivityPub/nostr/matrix/shared-DB CRDTs; design the signed-Git-bundle upgrade path now. (R03, `federation-architecture.md`)
- **Matching correction:** current lookup is fingerprint-only (`osh:<moviehash>` or local `jf:<ItemId>`); duration is stored, not checked in lookup. Asset/timeline binding and calibration are PROPOSED. Equal duration or sampled ends do not verify a cut (R04).
- **Taxonomy:** fixed 9 categories × severity 0–3 + action enum (mute/skip/mark; blur/crop schema-reserved, rendered as skip in v1). Default category→action map; profile resolves the actual action. (R05, R06, `03-CONCEPTS`, `tagging-taxonomy-and-data-model.md`)
- **Playback correction:** Web v10.11.11 defaults Unknown to None; the provider discards submitted action/category. Required actions on an exact tuple remain UNVERIFIED. Seek and Mute/Unmute commands exist but establish neither an interval scheduler nor device compliance. Kodi/mpv need distinct tested export adapters (R07).
- **Trust-boundary correction:** provider generation is user-blind; authenticated item lookup elsewhere is not. A metadata proxy can select intervals per user, not force actions or close original-media bypasses. Cooperative supported-client filtering comes first; server-side enforcement/bypass resistance is retained longer term, with scope and design still open (R13; owner sequencing decision 2026-10-08). Metadata filtering or remote commands alone are not unbypassable playback enforcement.
- **Segment write path:** PWA → cleanyfin's Go API (source of truth); plugin materializes at scan + hosts its own thin write controller; not Intro Skipper's route. Shipped `MediaSegmentDto` = `Id, ItemId, Type, StartTicks, EndTicks` only. (R14, `spike-b-segment-write-api.md`)
- **Publication/identity correction:** pending rows are public in exact/prefix/dump reads above the voting threshold; submitter strings are unauthenticated claims. Quarantine, authenticated continuity, curator authority and suggestion-only automation are PROPOSED, not current publication barriers (R08/R10).

## The Competitor (the opening)

`jacob-willden/jellyfin-plugin-moviecontentfilter` — the only Jellyfin-specific content-filter plugin, "very early development," single dev, no releases, **no crowdsourcing / moderation / federation / in-player marking.** That's almost certainly the "it definitely sucks" project. The broader `delight-im/MovieContentFilter` **standard** (.mcf/WebVTT + taxonomy) is real prior art to *interoperate* with, not dismiss. cleanyfin's proposed opening is community metadata, local household authority, curator distribution and easier marking; none establishes native per-profile enforcement or frictionless runtime behavior. (`05-EXISTING-WORK`, `prior-art-and-oss-competitors.md`)

## What's Next (see `20-ROADMAP`)

1. ~~Two feasibility spikes~~ **DONE 2026-07-21** (A→R13 enforcement, B→R14 write-path, C→R07 client-support; verified vs 10.11 source).
2. ~~Stand up the docs site~~ **DONE 2026-07-21** (`docs/` Astro+Starlight, 23 pages, build + smoke green). *Remaining:* a GitHub Pages deploy workflow once the repo is pushed.
3. ~~Decide the data license~~ **DECIDED 2026-07-21 → R15: CC0-1.0 (data) + AGPL-3.0-or-later (code).** `LICENSE` + `DATA-LICENSE` written. Seeding via auto-generation + original contributions (no BY-NC-SA ingest).
4. **Phase 3 IN PROGRESS.** Slice 1 (Go API + `docker compose up`) ✅ merged to main (PR #1). **Slice 2 ✅ DONE 2026-07-22** on branch `feat/slice-2-clients`: the C# `IMediaSegmentProvider` plugin (`plugin/`, `dotnet build` clean vs Jellyfin.Controller 10.11.11) + the marking PWA (`pwa/`, `bun run build` clean) + a CORS middleware on the API so they interoperate; CI gates `plugin-ci.yml` added. All three components now compile/build/test. **Slice 3 ✅ DONE 2026-07-22:** real **moviehash** fingerprint (R04) — the plugin computes the OpenSubtitles moviehash (`osh:`…) per file + exposes `GET /Cleanyfin/Fingerprint` so the PWA resolves the same fp; algorithm verified against hand-computed vectors; plugin + PWA rebuilt clean. **Slice 4 ✅ DONE 2026-07-22:** hash-prefix query (R08; no guaranteed anonymity set). **Slice 5 ✅ DONE 2026-07-23** (first successful parallel workflow — registered `cleanyfin-parallel`, 3 agents): `GET /api/v1/dump` for mirrors (R03), the plugin `POST /Cleanyfin/Segments` write controller (R14), and a docs/KB alignment sweep — all re-verified green. **Next (PROPOSED):** E1/E2/E4 test one playback/failure contract, E3 selected-source/timeline binding, and E5 authorized profiles/exceptions. The controller forwards submissions without proving immediate materialization. Historical compile/build/smoke entries above were not rerun and do not validate these prospective corrections.

**Retained longer-term track:** server-side enforcement/bypass resistance. [Roadmap](./20-ROADMAP.md) gates entry on a defined threat model and feasibility spike covering original-media routes/authorization, compatibility and cost; exit requires failure/recovery and applicable legal review evidence before any enforcement claim. No claim covers a server administrator or someone controlling the media.

## Deliberately NOT Doing Right Now

- Expanding protection promises before live playback, outage and authorization gates; isolated metadata development need not stop.
- Multi-writer protocols or compulsory Git; a public dump is not implemented federation.
- Blur/crop or silent unsupported-action conversion.
- Unverified native mute/export compatibility promises.
- Unscoped bypass permissions; authorized exceptions, expiry and private audit remain proposed.

## Pointers

- **Research deep-dives:** `knowledge-base/01-working/*.md` (6 files)
- **Repo:** https://github.com/cybersader/cleanyfin
- **Reference implementations to clone:** SponsorBlockServer, Intro Skipper, jellyfin-plugin-template, endrl/jellyfin-plugin-edl, intro-skipper/segment-editor
