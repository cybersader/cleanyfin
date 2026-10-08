# cleanyfin — Roadmap

> 🌳 **Live here** — an operating view, not a pointer stub. Phases with explicit exit criteria and a hard line on what is DEFERRED. Backed by the 2026-07-21 research fan-out (`../knowledge-base/01-working/`). When a phase completes or direction shifts, update this file + [FOCUS.md](./FOCUS.md) in the same session.

**North stars (every phase is checked against these):** metadata-only never media (R01), super-easy setup as a feature, simplify-first, build on upstream Jellyfin (R02). See [12-PRINCIPLES](./12-PRINCIPLES.md).

**Dated correction (2026-10-08):** July source spikes and R15 license selection are historical progress, not live filtering or legal validation. Code exists. Production protection promises remain gated on exact playback, refresh-failure and authorization evidence. **Owner-approved sequencing (2026-10-08):** dependable filtering on explicitly supported cooperative clients is the first-release target; server-side enforcement/deliberate-bypass resistance is retained LONGER TERM, not a first-release prerequisite. The rest of the [reviewed staged roadmap](../knowledge-base/01-working/long-term-architecture-2026-10-07/05-roadmap-and-experiments.md) remains PROPOSED; E1–E12 were not run. This decision chooses no client/actions, enforcement technique, failure policy, exposure tolerance, timing/SLO targets, trust/privacy or recovery/maintenance budgets, public launch or legal clearance. Historical build/smoke results below were not rerun.

---

## Phase 0 — Research + Knowledge-Ops (DONE 2026-07-21)

Six-dimension research fan-out complete (legal, prior-art, Jellyfin integration, federation, tech-stack, taxonomy); findings written to `../knowledge-base/01-working/`; `.claude/` orientation layer synthesized; decisions R01–R12 logged in [41-QUESTIONS-RESOLVED](./41-QUESTIONS-RESOLVED.md).

**Exit criteria (met):** identity + v1 architecture provisionally locked; the two feasibility unknowns and the license decision explicitly named as gates.

---

## Phase 1 — Historical source spikes complete; runtime/product gates reopened 2026-10-08

**Spike A — Enforcement model. ✅ DONE → R13.** Verified from 10.11 source: provider generation carries no per-user context (`GetMediaSegments` is user-blind by contract; the read path returns the global set). Per-profile enforcement is therefore **not** obtainable from the provider system. Verdict: default = global provider + honest client-side opt-in; **optional** cleanyfin reverse-proxy filters the `/MediaSegments` response per authenticated user (per-user metadata selection only; client action and original-media access remain outside that boundary); avoid the fragile `ISessionManager` seam (broke in 10.11). See `../knowledge-base/01-working/spike-a-enforcement.md`.

**Spike B — Segment write path. ✅ DONE → R14.** Verified: core Jellyfin has **no** segment write endpoint; the community route was folded into Intro Skipper + coupled to its DB. Verdict: PWA → cleanyfin's Go API (source of truth); plugin materializes segments at scan + hosts its own thin write controller; the current controller forwards to the API without immediate native materialization; don't depend on Intro Skipper's route. Correction: shipped `MediaSegmentDto` = `Id, ItemId, Type, StartTicks, EndTicks` only. See `../knowledge-base/01-working/spike-b-segment-write-api.md`.

**Spike C — Historical client study (R07), not a Cleanyfin compatibility matrix.** Web v10.11.11 defaults Unknown to None and has short-span/replay guards. Required actions on each exact tuple remain UNVERIFIED. Seek and Mute/Unmute commands exist, but not an automatic interval scheduler. Kodi and mpv require distinct export adapters/tests.

**Data-license decision (BEFORE seeding) — ✅ DECIDED 2026-07-21 → R15: `CC0-1.0` (dataset) + `AGPL-3.0-or-later` (code).** `LICENSE` + `DATA-LICENSE` committed. Consequence: no bulk ingest of CC-BY-NC-SA data (SponsorBlock/MCF); cold-start via auto-generation + original contributions; interoperate with the `.mcf`/EDL **formats** only (R11).

**Historical exit:** source spikes and license selection permitted initial slices. **Settled sequencing:** cooperative first release; enforcement longer term. **Still-open production gates:** first tuple/actions, exposure/timing tolerance, failure policy, outage behavior and authorization. Runtime Phase-3 exit remains unmet in inspected evidence.

---

## Phase 2 — Public home for the project (✅ DONE 2026-07-21, stood up alongside the spikes)

Docs site live at `docs/`: **Astro + Starlight** (base `/cleanyfin`, R12), 23 pages, splash landing + start-here + vision/design/project/research sections + the three spike write-ups; portagenty `docs`/`share-docs`/`tests` sessions wired. **`bun run build` green; all 5 Playwright smoke tests pass.** (Nova theme dropped for build resilience under Bun — default Starlight theme.)

**Exit criteria (met):** docs site builds + smoke-tests pass; the deep-dives + orientation layer are readable; landing + start-here contribution paths exist. **CI/CD wired 2026-07-21:** `.github/workflows/deploy-docs.yml` (build+deploy to Pages, OIDC) and `.github/workflows/ci.yml` (build + Playwright smoke gate on PRs), action versions verified current. *Remaining (one manual step):* on first push, set repo **Settings → Pages → Source = "GitHub Actions"** — then `docs/**` pushes to `main` auto-deploy to https://cybersader.github.io/cleanyfin/.

---

## Phase 3 — Thin vertical slice (first code) — IN PROGRESS

**Owner-approved first-release target:** dependable filtering on explicitly supported cooperative clients; actual playback correctness remains UNVERIFIED and gated below. No first client or action set is selected by the sequencing decision.

A demoable end-to-end skip, boring and minimal:
- **Segment API (Go): ✅ slice 1 DONE 2026-07-21** (`server/`, branch `feat/segment-api`). Single binary, `modernc.org/sqlite` (WAL), stdlib `net/http` routing, `slog`. Endpoints: `/healthz`, `/readyz`, `GET/POST /api/v1/segments` (fingerprint-keyed, R04), `POST .../vote` with auto-hide ≤ −2 (R08), fixed taxonomy validation (R05/R06). **Verified:** `go vet`/`go test` green + full `docker compose up` smoke (submit→query→validate→downvote→hide). CI gate added (`server-ci.yml`). *Deferred to later slices:* hash-prefix privacy query, release/calibration + curator/profile tables, public dumps, `embed.FS` PWA hosting.
- **API-only golden path: historically DONE; full API/UI/plugin/player installation UNVERIFIED** — one `docker compose up -d --build` (SQLite on a named volume, `restart: unless-stopped`, `/healthz`), verified Healthy. *Still to add:* no-Docker binary + systemd alternative.
- **Plugin: ✅ slice 2 DONE 2026-07-22** (`plugin/`, branch `feat/slice-2-clients`). Thin C# `IMediaSegmentProvider` (`Jellyfin.Controller` 10.11.11 / net9.0) whose `GetMediaSegments` fetches from the Go API by fingerprint and emits native segments (R02); config page for the API URL; `build.yaml` + `manifest.json` repo template; CI gate `plugin-ci.yml`. **Verified:** `dotnet build -c Release` clean (0 warn/0 err) via the SDK container. *Note:* segments emit as `MediaSegmentType.Unknown` (no filter type); global-per-item, no per-profile enforcement yet (R13).
- **Marking PWA: ✅ slice 2 DONE 2026-07-22** (`pwa/`). Vite + TS, polls `/Sessions` `PlayState.PositionTicks` (ticks/10000 = ms), stamps in/out + category/severity/action, POSTs to the API. **Verified:** `bun run build` (strict `tsc` + vite) clean. Added a CORS middleware to the API so the PWA can call it cross-origin.
- **Release fingerprint (moviehash): ✅ slice 3 DONE 2026-07-22** (R04). The plugin computes the OpenSubtitles **moviehash** (`osh:` + filesize/first+last-64KiB checksum) of each file as the fingerprint, replacing the `jf:ItemId` placeholder, and exposes `GET /Cleanyfin/Fingerprint?itemId=...` so the PWA resolves the *same* fp (the browser can't read file bytes). **Verified:** plugin `dotnet build` clean + moviehash checked against hand-computed vectors (zero-file, first/last-word); PWA `bun run build` clean. *Still open (later slice):* cross-rip **calibration offset** for differently-encoded copies (audio-anchor).
- **Hash-prefix privacy query (k-anonymity): ✅ slice 4 DONE 2026-07-22** (R08). `GET /api/v1/segments/hash/{prefix}` returns segments for all fingerprints whose SHA-256 hex shares a 4–16 char prefix (grouped by fingerprint; client filters locally), without guaranteeing anonymity: sparse buckets and dictionary linkage can identify the work; the plugin currently uses exact lookup. Added a `fingerprint_hash` column (indexed, backfilled on migrate). **Verified:** `go vet` + `go test` green (incl. a test that submits under a fp, computes its SHA-256 prefix, and asserts retrieval; bad prefix → 400).
- **Data dump (R03) + plugin write controller (R14): ✅ slice 5 DONE 2026-07-23** (built via the registered `cleanyfin-parallel` workflow — 3 agents, disjoint dirs, each self-verified). `GET /api/v1/dump` returns all visible segments for **mirrors/federation** (R03, SponsorBlock public-dump model); the plugin's `POST /Cleanyfin/Segments` resolves the fingerprint and **forwards a submission to the cleanyfin API** through Jellyfin auth (R14, forwarding only; immediate materialization UNVERIFIED). The `.claude/` architecture + data-model docs and their docs-site pages were re-aligned to shipped reality (incl. an "Implementation status" section noting what's live vs still-schematic). **Verified:** server `go test` + plugin `dotnet build` + docs `bun run build` all green.

**Exit criteria (not established):** E1/E2/E4/E5 demonstrate the selected tuple, required actions, measured timing envelope, refresh failure states and authorization; E6 separately proves complete release installation. API-only Compose smoke is insufficient. Unknown is experimental, not default filtering.

---

## Phase 4 — Crowdsourcing + interop + seed

**PROPOSED:** authenticated pseudonymous continuity, explicit quarantine/curation and bounded review. Current pending rows are public; votes are not household authority. Player-specific import/export requires separate format/action tests. Rights/consent must be verified per source; R15 does not approve every import or legacy record.

**Exit criteria:** a non-owner can submit + vote without an account; moderation thresholds enforce; a non-empty seed DB imported under the chosen license; MCF + EDL round-trip verified.

---

## Phase 5 — Federation + curators

**IMPLEMENTED:** visible-only public dump. **PROPOSED:** origin-scoped complete snapshots, validated staging, atomic dataset-plus-acceptance-history activation and private overlays; optional mirrors/deltas when measured. A dump is neither a full backup nor a replication protocol. Curator approvals bind reviewed revisions; mutation/deletion/reappearance must not silently transfer approval.

**Exit criteria:** a full public dump downloadable; a 5-minute "stand up a read-only mirror" guide works end-to-end; a household can subscribe to a curator profile and see its locked segments win precedence.

---

## Longer-term track — Server-side enforcement / deliberate-bypass resistance

**RETAINED product objective by owner decision, 2026-10-08.** Not abandoned and not a prerequisite for the cooperative first release; no implementation approach or delivery date is approved.

**Entry criteria:** define an explicit threat model and authorization boundary; scope a feasibility spike accounting for original-media access, alternate clients, downloads and file shares, client/format compatibility and operating cost. Scope must be defined in later design; no enforceability against a server administrator or someone controlling the media is claimed.

**Exit criteria before enforcement claims:** evidence from that spike and scoped tests establishes the declared access/authorization boundary, client/format compatibility and affordable operation, reliable failure/recovery behavior and applicable legal review. Metadata filtering or remote commands alone are not unbypassable playback enforcement. This track grants neither DRM/circumvention scope nor legal clearance; actual technique and budgets remain open.

---

## Deliberately DEFERRED (outside current release scope)

- **Playback expansion:** mute/skip/mark, exports, casting and downloads need exact adapter tests; remote commands are not automatic filtering.
- **Blur/crop and local transforms:** deferred; no silent substitution or legal clearance.
- **Multi-writer/CRDTs:** deferred; a durable outbox is a table, not a CRDT requirement.
- **Alignment:** explicit asset/timeline verification first; current moviehash lookup omits duration and proves no cut equivalence.
- **Exceptions:** authorized scope, expiry, revocation and audit remain proposed.
- **Escalation:** export bandwidth may motivate deltas; database contention may independently motivate database changes. No row-count threshold or mandatory deltas-first ladder establishes feasibility.

See [40-QUESTIONS-OPEN](./40-QUESTIONS-OPEN.md) for the decisions a maintainer still owns, and [31-TRADEOFFS](./31-TRADEOFFS.md) for the honest tensions behind these cuts.
