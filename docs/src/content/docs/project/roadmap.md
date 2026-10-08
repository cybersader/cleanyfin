---
title: Roadmap
description: Phased plan with explicit exit criteria and a hard line on what is deferred, backed by the 2026-07-21 research fan-out.
sidebar:
  order: 1
---

:::caution[Correction 2026-10-08 — source progress is not runtime validation]
Components exist; R15 records CC0 data/AGPL code. July spikes/builds are historical evidence, not current playback, installation, recovery or legal validation. Production protection promises remain gated. **Owner-approved sequencing (2026-10-08):** dependable filtering on explicitly supported cooperative clients is the first-release target; server-side enforcement/deliberate-bypass resistance is retained LONGER TERM, not a first-release prerequisite. The rest of the research roadmap (repository file: `knowledge-base/01-working/long-term-architecture-2026-10-07/05-roadmap-and-experiments.md`) remains PROPOSED; E1–E12 and builds were not run here. No client/actions, technique, failure policy, exposure tolerance, timing/SLO targets, trust/privacy or recovery/maintenance budgets, public launch or legal clearance are approved by this sequencing decision. The research file is not a published docs-site route.
:::

> 🌳 **Live here** — an operating view, not a pointer stub. Phases with explicit exit criteria and a hard line on what is DEFERRED. Backed by the 2026-07-21 research fan-out (the six deep-dives under `knowledge-base/01-working/`). When a phase completes or direction shifts, update this file + FOCUS.md in the same session.

**North stars (every phase is checked against these):** metadata-only never media (R01), super-easy setup as a feature, simplify-first, build on upstream Jellyfin (R02). See [Principles](/cleanyfin/vision/principles/).

**Sequencing rule:** no production code until the two Phase-1 spikes resolve *and* the data-license is chosen. This is deliberate — building the wrong enforcement model or seeding under a conflicting license is expensive to undo.

---

## Phase 0 — Research + Knowledge-Ops (DONE 2026-07-21)

Six-dimension research fan-out complete (legal, prior-art, Jellyfin integration, federation, tech-stack, taxonomy); findings written to `knowledge-base/01-working/`; `.claude/` orientation layer synthesized; decisions R01–R12 logged in [Decisions (resolved)](/cleanyfin/project/decisions/).

**Exit criteria (met):** identity + v1 architecture provisionally locked; the two feasibility unknowns and the license decision explicitly named as gates.

---

## Phase 1 — Historical spikes; runtime/product gates reopened 2026-10-08

Source investigations below are historical questions, not live-test results or a reopened license selection. R15 remains recorded; cooperative first-release scope is owner-approved, with enforcement retained longer term. Exact tuple/actions, exposure tolerance and failure behavior remain open.

**Spike A — Enforcement model.** Can a Jellyfin 10.11 plugin actually enforce per-profile skip/mute on the server-side playback path, or is enforcement only client-cooperative (client reads segments, client decides the action)? Segments are global per media item and actions are chosen per-client, so "per-profile filtering" may not be natively enforceable ([jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/) F10, R5). This determines whether the plugin is the *enforcement point* or just a *segment/EDL provider*. Relates to (R02) and the open per-profile question.
- *Correction:* provider generation is user-blind. Metadata selection is not enforcement; original-media access bypasses it. Owner chose cooperative filtering first and retained server-side enforcement/bypass resistance longer term; actual scope/design and evidence gates precede enforcement claims.

**Spike B — Segment write path.** Confirm the exact Jellyfin 10.11+ HTTP route + payload to CREATE and DELETE Media Segments, now that `jellyfin-plugin-ms-api` was absorbed into Intro Skipper ([jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/) F8). Needed for the marking PWA's submit path. Inspected core exposes reads, not this shipped write contract. MediaSegmentDto has Id, ItemId, Type, StartTicks, EndTicks, not StreamIndex/Action/Comment. Current custom controller forwards to the API without establishing immediate materialization.
- *Exit:* a confirmed request/response contract (route, payload, auth) captured against a running 10.11 server.

**Recorded license decision (R15, 2026-07-21).** CC0-1.0 data/AGPL-3.0-or-later code were selected. Verify rights/consent per import; historical alternatives below do not reopen that choice. CC0/CC-BY (frictionless) vs the CC-BY-NC-SA of MCF and SponsorBlock seed data — these **conflict**, so importing seed data constrains our own license (R11, Q40). Code license lean: AGPL-3.0.
- *Exit:* a written code-license + data-license pair, with a note on which seed sources are compatible.

**Historical exit:** source spikes/license selection enabled initial code. **Reopened gates:** live Unknown/actions, timing tolerance, refresh preservation and authorization; documentation changes clear none of them.

---

## Phase 2 — Docs source exists; earlier build/smoke progress not rerun

Stand up the docs site: **Astro + Starlight** at `docs/` (the sibling-project convention, R12), portagenty `docs`/`share-docs`/`tests` sessions already wired. Publish the research so contributors have a canonical public home.

**Exit criteria:** docs site builds + deploys; the six deep-dives + this orientation layer are readable publicly; a "how to contribute" landing page exists.

---

## Phase 3 — Thin vertical slice (first code)

**Owner-approved first-release target:** dependable filtering on explicitly supported cooperative clients; actual playback correctness remains UNVERIFIED and gated below. No first client or action set is selected by the sequencing decision.

A demoable end-to-end skip, boring and minimal:
- **IMPLEMENTED API:** Go/SQLite exact/prefix reads, submit/vote and visible dump. Pending is public; submitter labels unauthenticated; prefix lookup guarantees no anonymity. Embedded UI/curated views remain PROPOSED.
- **Golden path:** historical Compose smoke covered API only. Full API/UI/plugin/player release installation, binary/systemd and verified restore remain UNVERIFIED.
- **IMPLEMENTED plugin:** net9.0/Jellyfin.Controller 10.11.11 emits Unknown and discards action/category; Web v10.11.11 defaults Unknown to None. No broad native-client or complete-release guarantee follows.
- **Marking PWA:** minimal, reads live `/Sessions` `PlayState.PositionTicks` (ticks/10,000,000 = seconds), stamps in/out + category, POSTs a segment via the Spike-B contract.

**Exit criteria (unmet in inspected evidence):** exact tuple/action contract passes E1/E2/E4/E5; E6 proves complete installation. Include ordinary/forced refresh failure and source/profile transitions. API smoke/historical CI are insufficient.

---

## Phase 4 — Crowdsourcing + interop + seed

**PROPOSED:** explicit curated publication, authenticated pseudonymous continuity and bounded review; legacy-publication transition is owner-controlled. Distinct Kodi/mpv adapters need tests. R15 is not rights clearance for imported or automated data.

**Exit criteria:** a non-owner can submit + vote without an account; moderation thresholds enforce; a non-empty seed DB imported under the chosen license; MCF + EDL round-trip verified.

---

## Phase 5 — Federation + curators

**IMPLEMENTED:** visible dump. **PROPOSED:** complete origin-scoped snapshots, key-to-scope authorization, atomic dataset-plus-acceptance-history activation and revision-bound private curation; optional mirrors/deltas when measured. Dump download is neither backup nor replication.

**Exit criteria:** a full public dump downloadable; a 5-minute "stand up a read-only mirror" guide works end-to-end; a household can subscribe to a curator profile and see its locked segments win precedence.

---

## Longer-term track — Server-side enforcement / deliberate-bypass resistance

**RETAINED product objective by owner decision, 2026-10-08.** Not abandoned and not a prerequisite for the cooperative first release; no implementation approach or delivery date is approved.

**Entry criteria:** define an explicit threat model and authorization boundary; scope a feasibility spike accounting for original-media access, alternate clients, downloads and file shares, client/format compatibility and operating cost. Scope must be defined in later design; no enforceability against a server administrator or someone controlling the media is claimed.

**Exit criteria before enforcement claims:** evidence from that spike and scoped tests establishes the declared access/authorization boundary, client/format compatibility and affordable operation, reliable failure/recovery behavior and applicable legal review. Metadata filtering or remote commands alone are not unbypassable playback enforcement. This track grants neither DRM/circumvention scope nor legal clearance; actual technique and budgets remain open.

---

## Deliberately DEFERRED (outside current release scope)

- **Playback expansion:** actions, exports, casting/downloads require exact adapter tests; remote commands are not automatic filtering.
- **Blur/crop/transforms:** deferred; no silent substitution or legal clearance.
- **Multi-writer/CRDTs:** deferred; durable outbox needs no CRDT.
- **Alignment:** verified asset/timeline binding first; current lookup omits duration and cut verification.
- **Exceptions:** authorization, scope, expiry, revocation and audit remain proposed.
- **Escalation:** bandwidth may motivate deltas; contention may independently motivate database changes. No row-count threshold or mandatory deltas-first ladder proves feasibility.

See [Open Questions](/cleanyfin/project/open-questions/) for the decisions a maintainer still owns, and [Trade-offs](/cleanyfin/project/tradeoffs/) for the honest tensions behind these cuts.
