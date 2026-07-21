---
title: Roadmap
description: Phased plan with explicit exit criteria and a hard line on what is deferred, backed by the 2026-07-21 research fan-out.
sidebar:
  order: 1
---

:::note[Update 2026-07-21 — spikes resolved]
The two Phase-1 feasibility spikes are **done** (verified vs Jellyfin 10.11 source): **enforcement** ([Spike A](/cleanyfin/research/spike-a-enforcement/) → R13) and **segment write path** ([Spike B](/cleanyfin/research/spike-b-segment-write-api/) → R14), plus a wider client-support picture ([Spike C](/cleanyfin/research/spike-c-client-support/) → R07 updated). The **docs site is live** (this site). The sole remaining gate before code is the **data-license** decision.
:::

> 🌳 **Live here** — an operating view, not a pointer stub. Phases with explicit exit criteria and a hard line on what is DEFERRED. Backed by the 2026-07-21 research fan-out (the six deep-dives under `knowledge-base/01-working/`). When a phase completes or direction shifts, update this file + FOCUS.md in the same session.

**North stars (every phase is checked against these):** metadata-only never media (R01), super-easy setup as a feature, simplify-first, build on upstream Jellyfin (R02). See [Principles](/cleanyfin/vision/principles/).

**Sequencing rule:** no production code until the two Phase-1 spikes resolve *and* the data-license is chosen. This is deliberate — building the wrong enforcement model or seeding under a conflicting license is expensive to undo.

---

## Phase 0 — Research + Knowledge-Ops (DONE 2026-07-21)

Six-dimension research fan-out complete (legal, prior-art, Jellyfin integration, federation, tech-stack, taxonomy); findings written to `knowledge-base/01-working/`; `.claude/` orientation layer synthesized; decisions R01–R12 logged in [Decisions (resolved)](/cleanyfin/project/decisions/).

**Exit criteria (met):** identity + v1 architecture provisionally locked; the two feasibility unknowns and the license decision explicitly named as gates.

---

## Phase 1 — De-risk BEFORE committing architecture (NEXT)

Three gating investigations. No golden-path code until these close.

**Spike A — Enforcement model.** Can a Jellyfin 10.11 plugin actually enforce per-profile skip/mute on the server-side playback path, or is enforcement only client-cooperative (client reads segments, client decides the action)? Segments are global per media item and actions are chosen per-client, so "per-profile filtering" may not be natively enforceable ([jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/) F10, R5). This determines whether the plugin is the *enforcement point* or just a *segment/EDL provider*. Relates to (R02) and the open per-profile question.
- *Exit:* a written verdict — server-side-enforceable, or client-cooperative-only — plus the chosen v1 stance and its honest trust boundary.

**Spike B — Segment write path.** Confirm the exact Jellyfin 10.11+ HTTP route + payload to CREATE and DELETE Media Segments, now that `jellyfin-plugin-ms-api` was absorbed into Intro Skipper ([jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/) F8). Needed for the marking PWA's submit path. Assume a POST taking ItemId, Type, StartTicks, EndTicks (+ StreamIndex) behind an admin/API token; verify against the Intro Skipper 10.11 source + api.jellyfin.org OpenAPI.
- *Exit:* a confirmed request/response contract (route, payload, auth) captured against a running 10.11 server.

**Data-license decision (BEFORE seeding).** Choose the dataset license *before* importing any seed data. CC0/CC-BY (frictionless) vs the CC-BY-NC-SA of MCF and SponsorBlock seed data — these **conflict**, so importing seed data constrains our own license (R11, Q40). Code license lean: AGPL-3.0.
- *Exit:* a written code-license + data-license pair, with a note on which seed sources are compatible.

**Phase 1 exit criteria:** both spike verdicts written + the license pair chosen. Only then does Phase 3 code begin.

---

## Phase 2 — Public home for the project (can run parallel to Phase 1)

Stand up the docs site: **Astro + Starlight** at `docs/` (the sibling-project convention, R12), portagenty `docs`/`share-docs`/`tests` sessions already wired. Publish the research so contributors have a canonical public home.

**Exit criteria:** docs site builds + deploys; the six deep-dives + this orientation layer are readable publicly; a "how to contribute" landing page exists.

---

## Phase 3 — Thin vertical slice (first code)

A demoable end-to-end skip, boring and minimal:
- **Segment API (Go):** single static binary, `modernc.org/sqlite`, `embed.FS` serving the PWA, SQLite-WAL default. GET (with hash-prefix variant), POST submit, POST vote. (R05, [tech-stack-and-devops](/cleanyfin/research/tech-stack/))
- **Golden path:** one `docker compose up` (SQLite file on a named volume, `restart: unless-stopped`, `/healthz`) + a no-Docker binary + systemd alternative.
- **Plugin:** clone `jellyfin-plugin-template`; thin C# `IMediaSegmentProvider` whose `GetMediaSegments` fetches from the Go API and emits native segments (R02). Ship a `manifest.json` repo.
- **Marking PWA:** minimal, reads live `/Sessions` `PlayState.PositionTicks` (ticks/10,000,000 = seconds), stamps in/out + category, POSTs a segment via the Spike-B contract.

**Exit criteria:** on a real 10.11 server, a segment marked in the PWA is skipped by a native Web/Android TV client via the plugin — one `docker compose up`, no manual DB steps.

---

## Phase 4 — Crowdsourcing + interop + seed

Turn the slice into a community DB: submit / vote / moderate pipeline (account-free pseudonymous IDs, auto-hide at vote ≤ −2, shadowban, curator-lock — R08). MCF (.mcf/WebVTT) + Kodi EDL **import and export** (R11); EDL export gives real mute on Kodi/mpv (R07). Seed the DB from license-compatible open sources; automation writes `status='auto_suggested'` only, human-gated to `published` (R10).

**Exit criteria:** a non-owner can submit + vote without an account; moderation thresholds enforce; a non-empty seed DB imported under the chosen license; MCF + EDL round-trip verified.

---

## Phase 5 — Federation + curators

Publish the full dataset as periodic public dumps; make read-only **mirrors** a first-class, documented feature (sb-mirror pattern, R03). Ship subscribable **curator profiles** inside the one open dataset (subsidiarity without ActivityPub, R09). Design the dump format as signable, curator-scoped bundles now so the signed-Git-bundle upgrade path stays open.

**Exit criteria:** a full public dump downloadable; a 5-minute "stand up a read-only mirror" guide works end-to-end; a household can subscribe to a curator profile and see its locked segments win precedence.

---

## Deliberately DEFERRED (not in the current plan)

- **Native mute** on Web/Android TV — upstream-gated; Jellyfin has no client mute action as of 10.11 ([jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/) F5, R07). Ship skip + EDL-mute; add native mute when jellyfin-meta #30 lands.
- **Blur / crop / visual masking** for nudity — no Jellyfin primitive exists (F6); schema-reserved, rendered as skip in v1 (R05).
- **True S2S federation** — ActivityPub / nostr / matrix / shared-DB CRDT sync. Mirrors + public dumps *are* v1 federation (R03, R06). CRDT only as an offline outbox inside the marking client.
- **Chromaprint audio-anchor auto-align** — cross-rip offset transfer is v2 opt-in; v1 is exact-file match via moviehash + duration, fail-safe on low confidence (R04).
- **Push-notification bypass approval** — v1 = admin dashboard toggle with expiry.
- **Postgres** — SQLite until SponsorBlock scale (millions of segments + sustained concurrent writers); keep the schema portable (R05).

See [Open Questions](/cleanyfin/project/open-questions/) for the decisions a maintainer still owns, and [Trade-offs](/cleanyfin/project/tradeoffs/) for the honest tensions behind these cuts.
