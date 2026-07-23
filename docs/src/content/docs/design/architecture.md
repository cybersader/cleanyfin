---
title: Architecture
description: How cleanyfin's three thin components fit around one self-hostable segment server, and the honest enforcement gaps.
sidebar:
  order: 2
---

> How the three components fit and why. Backed by [Jellyfin integration mechanics](/cleanyfin/research/jellyfin/), [tech stack & DevOps](/cleanyfin/research/tech-stack/), and [federation architecture](/cleanyfin/research/federation/). Locked shape = (R02). The per-profile enforcement gap is still gated on Spike A — see [Roadmap](/cleanyfin/project/roadmap/).

## Overview — three thin pieces around one server

**The server + its open dataset are the product; the plugin and PWA are thin clients.** A thin C# `IMediaSegmentProvider` plugin pulls community-tagged segments from a small self-hostable Go API server and emits them as native Jellyfin Media Segments, so unmodified clients render skip buttons. A companion PWA reads live playback position and submits new segments back. Everything crossing the wire is timestamps + categories + edit-decisions — never A/V (R01, the legal keystone).

## Component diagram

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

_Response-filtering reverse-proxy for real per-profile enforcement (R13) is **deferred** — segments are still global per item; the plugin's own write controller (R14) and cross-rip calibration are later slices._

## How it works — the two loops

**Read (filter) loop.** The plugin's `GetMediaSegments(item)` resolves the local file to a release **fingerprint** — the real OpenSubtitles **moviehash** (`osh:` + filesize/first+last-64 KiB, `jf:<ItemId>` fallback, R04) — then fetches matching community segments over `GET /api/v1/segments?fp=<fingerprint>` (or the privacy-preserving hash-prefix query, below) and emits native Jellyfin Media Segments. Because no content-filter segment *type* exists, each is emitted as `MediaSegmentType.Unknown` and the real category/action stays in cleanyfin's own DB (R14). Clients (Web full; Android TV 0.18+) render Skip / Ask-to-skip natively — the plugin does not touch client UI, exactly like Intro Skipper ([Jellyfin integration mechanics](/cleanyfin/research/jellyfin/) F3–F4). Category → action is a default on the segment, resolved to the real action by the viewer's profile at playback (R06).

**Write (mark) loop.** The PWA authenticates to Jellyfin, polls `/Sessions` for the active `PlayState.PositionTicks` ([tech stack & DevOps](/cleanyfin/research/tech-stack/) F6), resolves the file's fingerprint by calling the plugin's `GET /Cleanyfin/Fingerprint?itemId=…` (the browser can't read file bytes, so the plugin computes the same moviehash the provider queries), lets the viewer stamp in/out + a category, and POSTs the segment to `POST /api/v1/segments`. It is a *side-car*, not a client plugin — there is no official Jellyfin client UI-extension API, so marking runs alongside the player ([Jellyfin integration mechanics](/cleanyfin/research/jellyfin/) F9, R4).

## Implementation status (2026-07-23)

What ships on `main` after Phase 3 slices 1–4 (the rest of this page describes the target design):

- **Go API** — `GET /healthz`, `GET /readyz`, `GET /api/v1/stats`; `GET /api/v1/segments?fp=<fingerprint>` (exact match, R04); `GET /api/v1/segments/hash/{prefix}` (4–16 hex chars of `SHA-256(fingerprint)`, k-anonymity — returns every fingerprint sharing the prefix grouped by fingerprint, client filters locally, so the server never learns the title, R08); `POST /api/v1/segments` (submit, fixed-taxonomy validated, R05/R06); `POST /api/v1/segments/{id}/vote` (auto-hide at score ≤ −2, R08). A CORS middleware (`CLEANYFIN_CORS_ORIGIN`, default `*`) lets the separate-origin PWA call it.
- **Plugin** — `CleanyfinSegmentProvider : IMediaSegmentProvider` (Jellyfin.Controller 10.11.11 / net9.0) fetches by fingerprint and emits `MediaSegmentType.Unknown`; a `FingerprintController` exposes `GET /Cleanyfin/Fingerprint?itemId=…` that computes the file's moviehash so the PWA submits under the *same* fingerprint.
- **PWA** — Vite + TypeScript static app; polls `/Sessions`, resolves the fingerprint via the plugin, POSTs marks.
- **Deferred:** the response-filtering reverse-proxy for real per-profile enforcement (R13) is **not** built — segments stay global per item; the plugin's own thin write controller (R14) and cross-rip calibration offset are later slices.

## Component boundaries

| Component | Language / tech | Role | Why fixed here |
|---|---|---|---|
| Plugin | C# / .NET (net8.0 for 10.10, net9.0 for 10.11) | `IMediaSegmentProvider`; pulls segments, emits native ones | Jellyfin plugins MUST be .NET DLLs — the only forced language boundary (R02) |
| API server | Go, single static binary, `modernc.org/sqlite`, `embed.FS` | The crowdsourced DB, submit/vote/moderation, serves the PWA | CGo-free single artifact = strongest "super-easy setup" story (Hard Constraint #2) |
| Marking PWA | Vite + TypeScript, static build | Reads live position, resolves the fingerprint via the plugin, submits segments | Static export embeds into the Go binary → one process, one port |

Distribution: plugin via a static `manifest.json` repo (GitHub Releases/Pages, auto-built in Actions); server via GHCR image / raw binary+systemd / `docker compose up`. See [Roadmap](/cleanyfin/project/roadmap/) Phase 3 and [Data Model](/cleanyfin/design/data-model/) for the segment schema.

## Limitations / honest gaps

- **Per-profile enforcement gap.** Segments are **global per item**, not per-user; segment *actions* are chosen per-client, not enforced as a server-side per-profile ACL ([Jellyfin integration mechanics](/cleanyfin/research/jellyfin/) F10, R5). So "per-profile category settings" and "per-title bypass" are **not** natively enforced. **v1 stance:** accept client-cooperative opt-in and be honest about the trust boundary; a real per-user enforcement layer is a fast-follow pending **Spike A** (does a 10.11 plugin enforce server-side, or only cooperate?). See [Roadmap](/cleanyfin/project/roadmap/).
- **No native mute.** Jellyfin has no client mute action as of 10.11 — only skip-style. VidAngel-style word-mute is not possible on native clients yet (R07). **v1 = SKIP-only** on Web + Android TV; skip drops both audio and video for the span.
- **EDL export = the real mute path.** For true per-segment mute (EDL action 1) or cut (action 0), export to Kodi/mpv/MPlayer via the EDL format ([Jellyfin integration mechanics](/cleanyfin/research/jellyfin/) F7, R07). Downside: needs a writeable library; generate on demand for opt-in Kodi/mpv users rather than as a default dependency.
- **No visual masking.** Blur/crop/black-box for nudity has no Jellyfin primitive (F6); schema-reserved, rendered as skip in v1 (R05).
- **Category ≠ Jellyfin type.** Jellyfin's segment-type enum is fixed (Intro/Outro/Recap/Preview/Commercial/Annotation) with no content categories — cleanyfin carries its rich taxonomy in its own DB and translates to the nearest Jellyfin type at emit time (F2). See [Data Model](/cleanyfin/design/data-model/).
- **Single-writer DB.** SQLite serializes writes; fine at v1 scale, graduate to Postgres only at SponsorBlock scale (stack lean; [tech stack & DevOps](/cleanyfin/research/tech-stack/)).

## Reference implementations to clone

Intro Skipper (provider pattern), `jellyfin-plugin-chapter-segments` (simplest `IMediaSegmentProvider`), `jellyfin-plugin-template` (C# scaffold + manifest CI), `endrl/jellyfin-plugin-edl` (EDL export), `intro-skipper/jellyfin-plugin-ms-api` (write-path reference), SponsorBlockServer + sb-mirror (server + dumps/mirrors). See [Prior Art](/cleanyfin/project/prior-art/).
