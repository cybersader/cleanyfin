# cleanyfin — Roadmap

> 🌳 **Live here** — an operating view, not a pointer stub. Phases with explicit exit criteria and a hard line on what is DEFERRED. Backed by the 2026-07-21 research fan-out (`../knowledge-base/01-working/`). When a phase completes or direction shifts, update this file + [FOCUS.md](./FOCUS.md) in the same session.

**North stars (every phase is checked against these):** metadata-only never media (R01), super-easy setup as a feature, simplify-first, build on upstream Jellyfin (R02). See [12-PRINCIPLES](./12-PRINCIPLES.md).

**Sequencing rule:** no production code until the two Phase-1 spikes resolve *and* the data-license is chosen. **Update 2026-07-21: both spikes are now RESOLVED (see below) — the sole remaining gate before code is the data-license decision (Q2).**

---

## Phase 0 — Research + Knowledge-Ops (DONE 2026-07-21)

Six-dimension research fan-out complete (legal, prior-art, Jellyfin integration, federation, tech-stack, taxonomy); findings written to `../knowledge-base/01-working/`; `.claude/` orientation layer synthesized; decisions R01–R12 logged in [41-QUESTIONS-RESOLVED](./41-QUESTIONS-RESOLVED.md).

**Exit criteria (met):** identity + v1 architecture provisionally locked; the two feasibility unknowns and the license decision explicitly named as gates.

---

## Phase 1 — De-risk BEFORE committing architecture (SPIKES DONE 2026-07-21; license gate open)

**Spike A — Enforcement model. ✅ DONE → R13.** Verified from 10.11 source: no seam in Jellyfin's segment pipeline carries per-user context (`GetMediaSegments` is user-blind by contract; the read path returns the global set). Per-profile enforcement is therefore **not** obtainable from the provider system. Verdict: default = global provider + honest client-side opt-in; **optional** cleanyfin reverse-proxy filters the `/MediaSegments` response per authenticated user (real enforcement on the stable public HTTP contract, metadata-only, one container); avoid the fragile `ISessionManager` seam (broke in 10.11). See `../knowledge-base/01-working/spike-a-enforcement.md`.

**Spike B — Segment write path. ✅ DONE → R14.** Verified: core Jellyfin has **no** segment write endpoint; the community route was folded into Intro Skipper + coupled to its DB. Verdict: PWA → cleanyfin's Go API (source of truth); plugin materializes segments at scan + hosts its own thin write controller for live insert; don't depend on Intro Skipper's route. Correction: shipped `MediaSegmentDto` = `Id, ItemId, Type, StartTicks, EndTicks` only. See `../knowledge-base/01-working/spike-b-segment-write-api.md`.

**Spike C — Client support + mute status. ✅ DONE → R07 updated.** Skip fleet is wider than assumed — Web + Android TV + Roku + Kodi (native), webOS partial, Swiftfin/iOS the gap. Native mute still doesn't exist anywhere (18 months); emit EDL from cleanyfin's own data for Kodi/mpv mute. See `../knowledge-base/01-working/spike-c-client-support.md`.

**Data-license decision (BEFORE seeding) — ✅ DECIDED 2026-07-21 → R15: `CC0-1.0` (dataset) + `AGPL-3.0-or-later` (code).** `LICENSE` + `DATA-LICENSE` committed. Consequence: no bulk ingest of CC-BY-NC-SA data (SponsorBlock/MCF); cold-start via auto-generation + original contributions; interoperate with the `.mcf`/EDL **formats** only (R11).

**Phase 1 exit:** spikes ✅ + license ✅ — **Phase 1 complete. Phase 3 (the thin vertical slice) is unblocked.**

---

## Phase 2 — Public home for the project (✅ DONE 2026-07-21, stood up alongside the spikes)

Docs site live at `docs/`: **Astro + Starlight** (base `/cleanyfin`, R12), 23 pages, splash landing + start-here + vision/design/project/research sections + the three spike write-ups; portagenty `docs`/`share-docs`/`tests` sessions wired. **`bun run build` green; all 5 Playwright smoke tests pass.** (Nova theme dropped for build resilience under Bun — default Starlight theme.)

**Exit criteria (met):** docs site builds + smoke-tests pass; the deep-dives + orientation layer are readable; landing + start-here contribution paths exist. **CI/CD wired 2026-07-21:** `.github/workflows/deploy-docs.yml` (build+deploy to Pages, OIDC) and `.github/workflows/ci.yml` (build + Playwright smoke gate on PRs), action versions verified current. *Remaining (one manual step):* on first push, set repo **Settings → Pages → Source = "GitHub Actions"** — then `docs/**` pushes to `main` auto-deploy to https://cybersader.github.io/cleanyfin/.

---

## Phase 3 — Thin vertical slice (first code) — IN PROGRESS

A demoable end-to-end skip, boring and minimal:
- **Segment API (Go): ✅ slice 1 DONE 2026-07-21** (`server/`, branch `feat/segment-api`). Single binary, `modernc.org/sqlite` (WAL), stdlib `net/http` routing, `slog`. Endpoints: `/healthz`, `/readyz`, `GET/POST /api/v1/segments` (fingerprint-keyed, R04), `POST .../vote` with auto-hide ≤ −2 (R08), fixed taxonomy validation (R05/R06). **Verified:** `go vet`/`go test` green + full `docker compose up` smoke (submit→query→validate→downvote→hide). CI gate added (`server-ci.yml`). *Deferred to later slices:* hash-prefix privacy query, release/calibration + curator/profile tables, public dumps, `embed.FS` PWA hosting.
- **Golden path: ✅ DONE** — one `docker compose up -d --build` (SQLite on a named volume, `restart: unless-stopped`, `/healthz`), verified Healthy. *Still to add:* no-Docker binary + systemd alternative.
- **Plugin: ✅ slice 2 DONE 2026-07-22** (`plugin/`, branch `feat/slice-2-clients`). Thin C# `IMediaSegmentProvider` (`Jellyfin.Controller` 10.11.11 / net9.0) whose `GetMediaSegments` fetches from the Go API by fingerprint and emits native segments (R02); config page for the API URL; `build.yaml` + `manifest.json` repo template; CI gate `plugin-ci.yml`. **Verified:** `dotnet build -c Release` clean (0 warn/0 err) via the SDK container. *Note:* segments emit as `MediaSegmentType.Unknown` (no filter type); global-per-item, no per-profile enforcement yet (R13).
- **Marking PWA: ✅ slice 2 DONE 2026-07-22** (`pwa/`). Vite + TS, polls `/Sessions` `PlayState.PositionTicks` (ticks/10000 = ms), stamps in/out + category/severity/action, POSTs to the API with `fingerprint = "jf:"+ItemId` (matches the plugin). **Verified:** `bun run build` (strict `tsc` + vite) clean. Added a CORS middleware to the API so the PWA can call it cross-origin.

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

- **Native mute** on Web/Android TV — upstream-gated; Jellyfin has no client mute action as of 10.11 (`jellyfin-integration-mechanics.md` F5, R07). Ship skip + EDL-mute; add native mute when jellyfin-meta #30 lands.
- **Blur / crop / visual masking** for nudity — no Jellyfin primitive exists (F6); schema-reserved, rendered as skip in v1 (R05).
- **True S2S federation** — ActivityPub / nostr / matrix / shared-DB CRDT sync. Mirrors + public dumps *are* v1 federation (R03, R06). CRDT only as an offline outbox inside the marking client.
- **Chromaprint audio-anchor auto-align** — cross-rip offset transfer is v2 opt-in; v1 is exact-file match via moviehash + duration, fail-safe on low confidence (R04).
- **Push-notification bypass approval** — v1 = admin dashboard toggle with expiry.
- **Postgres** — SQLite until SponsorBlock scale (millions of segments + sustained concurrent writers); keep the schema portable (R05).

See [40-QUESTIONS-OPEN](./40-QUESTIONS-OPEN.md) for the decisions a maintainer still owns, and [31-TRADEOFFS](./31-TRADEOFFS.md) for the honest tensions behind these cuts.
