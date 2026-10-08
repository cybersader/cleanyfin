---
title: Architecture
description: How cleanyfin's three thin components fit around one self-hostable segment server, and the honest enforcement gaps.
sidebar:
  order: 2
---

> How the three components fit and why. Backed by [Jellyfin integration mechanics](/cleanyfin/research/jellyfin/), [tech stack & DevOps](/cleanyfin/research/tech-stack/), and [federation architecture](/cleanyfin/research/federation/). Locked shape = (R02). Owner sequencing (2026-10-08): supported-client cooperative filtering first; server-side enforcement retained longer term, with scope/design still open — see [Roadmap](/cleanyfin/project/roadmap/).

## Dated source correction — 2026-10-08

This page separates implementation from historical targets. The reviewed proposal (repository file: `knowledge-base/01-working/long-term-architecture-2026-10-07/04-architecture-proposal.md`) is PROPOSED, not approved. Pinned evidence: Jellyfin/Web v10.11.11, accessed 2026-10-08. Live filtering, complete installation and recovery remain UNVERIFIED. The research file is not a published docs-site route.

## Overview — three thin pieces around one server

**The server + its open dataset are the product; the plugin and PWA are thin clients.** A thin C# `IMediaSegmentProvider` plugin pulls community-tagged segments from a small self-hostable Go API server and emits them as native Jellyfin Media Segments, as experimental Unknown intervals, without establishing native skip-button or default filtering behavior. A companion PWA reads live playback position and submits new segments back. Everything crossing the wire is timestamps + categories + edit-decisions — never A/V (R01, the legal keystone).

## Historical target diagram — not a shipped-component inventory

Native-skip, EDL, mirror, Litestream and binary/systemd arrows are proposed/unverified targets. Current provider uses exact fingerprint lookup, not the prefix route. Embedded UI, exports, import/replication and complete one-command installation are not established. Metadata-only distribution is not legal clearance.

```
                       SUBMIT: POST /api/v1/segments (fingerprint, start, end, category)
        ┌────────────────────────────────────────────────────────────────────┐
        │                                                                     │
        v                                                                     │
┌─────────────────────┐  GET /Sessions  +  GET /Cleanyfin/Fingerprint   ┌──────────────┐
│  Marking PWA         │── PlayState.PositionTicks (ticks / 10000 = ms) ─>│  Jellyfin    │
│  (Vite + TS,         │   fp resolved server-side by the plugin          │  server      │
│   static app)        │<─ NowPlayingItem / live position ────────────────│  10.11+      │
│  stamp in/out        │   fp = osh:<moviehash> (jf:<ItemId> fallback)    │              │
│  + category/sev      │                                                  │  ┌─────────┐ │
└──────────┬───────────┘                                                  │  │ cleanyfin│ │
           │                                                              │  │ plugin  │ │  native skip
           │  POST /api/v1/segments   ·   POST /segments/{id}/vote        │  │ (C#/.NET│─┼──> Web /
           │                                                              │  │  net9)  │ │   Android TV
           v                                                              │  └────┬────┘ │   clients
┌────────────────────────────────────────────┐   GetMediaSegments()          │      │
│  cleanyfin API SERVER  (Go, ONE static bin) │<── GET /api/v1/segments?fp= ──┘      │
│  ┌────────────────────────────────────────┐│      (or hash-prefix, k-anon)       │
│  │ GET ?fp=   ·   GET /…/hash/{prefix}    ││                                     │
│  │ POST submit   ·   POST /{id}/vote      ││   EDL export (action 1 = mute,      │
│  │ CORS · auto-hide ≤ −2 · healthz/readyz ││   0 = cut) ─────────────────────────┼──> Kodi /
│  └────────────────────────────────────────┘│                                     │     mpv
│  modernc.org/sqlite  (WAL, one file)        │                                     │
│  + optional Litestream sidecar (off-box DR) │   public dumps + read-only          │
└────────────────────────────────────────────┘   mirrors (sb-mirror) ─────────────┴──> peers
     one `docker compose up`  |  or binary + systemd
```

_Correction: a response proxy could select per-user metadata, not enforce playback. Segments remain global per item. Current custom controller forwards submissions; immediate insertion, policy and calibration are not established._

## How it works — the two loops

**Read (filter) loop.** The plugin's `GetMediaSegments(item)` resolves the local file to a release **fingerprint** — the real OpenSubtitles **moviehash** (`osh:` + filesize/first+last-64 KiB, `jf:<ItemId>` fallback, R04) — then fetches matching community segments over `GET /api/v1/segments?fp=<fingerprint>` (or the privacy-preserving hash-prefix query, below) and emits native Jellyfin Media Segments. Because no content-filter segment *type* exists, each is emitted as `MediaSegmentType.Unknown` and the real category/action stays in cleanyfin's own DB (R14). Web v10.11.11 defaults Unknown to None and requests enabled types only. Nonzero-start short-span/replay guards require testing; configured Unknown is not proven impossible. The provider discards action/category; no household-profile resolver is implemented. Native convenience-segment support is not a filtering guarantee.

**Write (mark) loop.** The PWA authenticates to Jellyfin, polls `/Sessions` for the active `PlayState.PositionTicks` ([tech stack & DevOps](/cleanyfin/research/tech-stack/) F6), resolves the file's fingerprint by calling the plugin's `GET /Cleanyfin/Fingerprint?itemId=…` (the browser can't read file bytes, so the plugin computes the same moviehash the provider queries), lets the viewer stamp in/out + a category, and POSTs the segment to `POST /api/v1/segments`. It is a *side-car*, not a client plugin — there is no official Jellyfin client UI-extension API, so marking runs alongside the player ([Jellyfin integration mechanics](/cleanyfin/research/jellyfin/) F9, R4).

## Implementation status (2026-07-23)

What ships on `main` after Phase 3 slices 1–4 (the rest of this page describes the target design):

- **Go API** — `GET /healthz`, `GET /readyz`, `GET /api/v1/stats`; `GET /api/v1/segments?fp=<fingerprint>` (exact match, R04); `GET /api/v1/segments/hash/{prefix}` (4–16 hex chars of `SHA-256(fingerprint)`, returns matching fingerprints grouped by fingerprint; local filtering reduces query specificity but guarantees no anonymity set, R08); `POST /api/v1/segments` (submit, fixed-taxonomy validated, R05/R06); `POST /api/v1/segments/{id}/vote` (auto-hide at score ≤ −2, R08). A CORS middleware (`CLEANYFIN_CORS_ORIGIN`, default `*`) lets the separate-origin PWA call it.
- **Plugin** — `CleanyfinSegmentProvider : IMediaSegmentProvider` (Jellyfin.Controller 10.11.11 / net9.0) fetches by fingerprint and emits `MediaSegmentType.Unknown`; a `FingerprintController` exposes `GET /Cleanyfin/Fingerprint?itemId=…` that computes the file's moviehash so the PWA submits under the *same* fingerprint.
- **PWA** — Vite + TypeScript static app; polls `/Sessions`, resolves the fingerprint via the plugin, POSTs marks.
- **Implemented since slice 5:** visible-only dump and plugin forwarding controller. Pending rows are public; API submitter strings are unauthenticated.
- **Proposed/unverified:** private policy, curated views, import/replication, calibration, embedded UI, release installation and recovery.
- **Refresh caveat (source inference):** ordinary refresh can delete existing provider materialization if an enabled supporting provider runs, catches fetch failure into empty output and deletion succeeds with a usable parent cancellation token. Force-overwrite pre-deletes all item rows; later insertion failure can leave incomplete replacement. Canonical API annotations are separate. E2 needs cancellation, disabled/no-refresh and mid-insertion controls; no live reproduction or preservation fix is established.

## Component boundaries

| Component | Language / tech | Role | Why fixed here |
|---|---|---|---|
| Plugin | C# / .NET (net8.0 for 10.10, net9.0 for 10.11) | `IMediaSegmentProvider`; pulls segments, emits native ones | Jellyfin plugins MUST be .NET DLLs — the only forced language boundary (R02) |
| API server | Go, single static binary, `modernc.org/sqlite`, `embed.FS` | The crowdsourced DB, submit/vote/moderation, serves the PWA | CGo-free single artifact = strongest "super-easy setup" story (Hard Constraint #2) |
| Marking PWA | Vite + TypeScript, static build | Reads live position, resolves the fingerprint via the plugin, submits segments | Static export embeds into the Go binary → one process, one port |

Distribution correction: compiled target is net9.0/Jellyfin.Controller 10.11.11; other ABI tuples remain unverified. Table entries for embedded UI/one-process distribution are proposed. Compose covers the API; release checksums/completeness and binary/systemd installation require E6. See [Roadmap](/cleanyfin/project/roadmap/) Phase 3 and [Data Model](/cleanyfin/design/data-model/) for the segment schema.

## Current limits — supersede historical bullets below

- Metadata selection or remote commands alone are not unbypassable playback enforcement. Owner-approved sequencing (2026-10-08) targets dependable filtering on explicitly supported cooperative clients first; playback correctness remains experiment-gated. Server-side enforcement/bypass resistance is retained longer term, not a first-release prerequisite. Threat model and design remain open; no claim covers a server administrator or someone controlling the media.
- Output is Unknown plus tick span, not nearest-category translation or rich actions. Pinned Web skip/prompt differs from session Seek/Mute/Unmute dispatch; no scheduler follows. Kodi/mpv require distinct adapters; writable-library mounts need not be mandatory.
- One Store connection serializes reads and writes; dumps accumulate the visible corpus in memory. Capacity remains unmeasured.
- modernc.org/sqlite v1.34.1 documents engine 3.46.0. WAL-reset requires multiple same-file connections and timed write/checkpoint/reset overlap; a sole connection or separate backup destination does not prove applicability. Proposed gate: inspect runtime engine/all actors and test a fixed driver before increasing concurrency. No current corruption is claimed.
- Use completed consistent SQLite backup or verified quiescence, preserve WAL and verify fresh application restore; public dump is not recovery state.

## Historical limitation bullets (superseded above; retained for traceability)

- **Per-profile enforcement gap.** Segments are **global per item**, not per-user; segment *actions* are chosen per-client, not enforced as a server-side per-profile ACL ([Jellyfin integration mechanics](/cleanyfin/research/jellyfin/) F10, R5). So "per-profile category settings" and "per-title bypass" are **not** natively enforced. **v1 stance:** accept client-cooperative opt-in and be honest about the trust boundary; a real per-user enforcement layer is a fast-follow pending **Spike A** (does a 10.11 plugin enforce server-side, or only cooperate?). See [Roadmap](/cleanyfin/project/roadmap/).
- **No native mute.** Jellyfin has no client mute action as of 10.11 — only skip-style. VidAngel-style word-mute is not possible on native clients yet (R07). **v1 = SKIP-only** on Web + Android TV; skip drops both audio and video for the span.
- **EDL export = the real mute path.** For true per-segment mute (EDL action 1) or cut (action 0), export to Kodi/mpv/MPlayer via the EDL format ([Jellyfin integration mechanics](/cleanyfin/research/jellyfin/) F7, R07). Downside: needs a writeable library; generate on demand for opt-in Kodi/mpv users rather than as a default dependency.
- **No visual masking.** Blur/crop/black-box for nudity has no Jellyfin primitive (F6); schema-reserved, rendered as skip in v1 (R05).
- **Category ≠ Jellyfin type.** Jellyfin's segment-type enum is fixed (Intro/Outro/Recap/Preview/Commercial/Annotation) with no content categories — cleanyfin carries its rich taxonomy in its own DB and translates to the nearest Jellyfin type at emit time (F2). See [Data Model](/cleanyfin/design/data-model/).
- **Single-writer DB.** SQLite serializes writes; fine at v1 scale, graduate to Postgres only at SponsorBlock scale (stack lean; [tech stack & DevOps](/cleanyfin/research/tech-stack/)).

## Reference implementations to clone

Intro Skipper (provider pattern), `jellyfin-plugin-chapter-segments` (simplest `IMediaSegmentProvider`), `jellyfin-plugin-template` (C# scaffold + manifest CI), `endrl/jellyfin-plugin-edl` (EDL export), `intro-skipper/jellyfin-plugin-ms-api` (write-path reference), SponsorBlockServer + sb-mirror (server + dumps/mirrors). See [Prior Art](/cleanyfin/project/prior-art/).
