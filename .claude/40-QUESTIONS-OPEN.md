# Open Questions — decisions a maintainer must still make

> 🌳 **Live here** — this is real content, not a pointer stub. These are the genuinely-unresolved calls gating real code, synthesized from every deep-dive's "Open Questions" plus the two feasibility spikes in [`20-ROADMAP`](./20-ROADMAP.md). Settled decisions live in [`41-QUESTIONS-RESOLVED`](./41-QUESTIONS-RESOLVED.md) (R01–R12); when one of these locks, move it there and update [`FOCUS.md`](./FOCUS.md) + [`PROJECT_CONTEXT.md`](./PROJECT_CONTEXT.md) in the same session.
>
> Snapshot date: 2026-07-21. Each item carries a **current lean** — a research-backed default, not a commitment.

## Resolved gates (was: Blocking)

> Update 2026-07-21: **all blocking gates are cleared.** The two Spikes (Q3, Q4) resolved via R13/R14; the **data license (Q2) is decided → R15.** Production code (the thin vertical slice) is unblocked — see [`20-ROADMAP`](./20-ROADMAP.md) Phase 3.

### Q2 — DATA license — ✅ RESOLVED 2026-07-21 (→ R15)
**Decision: `CC0-1.0` for the dataset + `AGPL-3.0-or-later` for the code.** Data is factual (thin copyright); CC0 maximizes federation/mirror/reuse and kills the NC ambiguity; give-back protection sits on the AGPL server code. *Accepted consequence:* cannot bulk-ingest CC-BY-NC-SA data (SponsorBlock/MCF) — cold-start via automated subtitle generation + original contributions; interoperate with the `.mcf`/EDL **formats** only (R11). Files: `LICENSE`, `DATA-LICENSE`. Verify any third-party seed set's license (e.g. VideoSkip) before importing.

### Q3 — Enforcement: per-profile server-side? — ✅ RESOLVED 2026-07-21 (Spike A → R13)
**Answer (verified from 10.11 source):** no seam in Jellyfin's segment pipeline carries per-user context, so per-profile enforcement is **not** obtainable from the provider system. Decision (R13): default install = global provider + honest client-side opt-in; **optional** cleanyfin reverse-proxy that filters the `/MediaSegments` response per authenticated user for real per-profile enforcement on the stable public HTTP contract; avoid the fragile `ISessionManager` seam. See `spike-a-enforcement.md`.
- *Residual (runtime verification, not blocking):* exact `/MediaSegments` response JSON shape on 10.11.x; whether any client caches segments across profile switches; whether a plugin response-filter/middleware could collapse the proxy into the plugin. Listed in the spike's "Open" section.

### Q4 — Exact 10.11+ segment write API — ✅ RESOLVED 2026-07-21 (Spike B → R14)
**Answer (verified):** core Jellyfin has **no** segment write endpoint; the community route was folded into Intro Skipper and coupled to its DB. Decision (R14): PWA writes to cleanyfin's Go API; plugin materializes segments and hosts its own thin write controller; don't depend on Intro Skipper's route. **Correction:** shipped `MediaSegmentDto` = `Id, ItemId, Type, StartTicks, EndTicks` only (no `StreamIndex`/`Action`/`Comment`). See `spike-b-segment-write-api.md`.

## Structural (shape the architecture; decide before/at skeleton)

### Q1 — Primary server language: Go vs. .NET vs. Node/TS
- **Lean: Go** — best single-static-binary/deploy + resilience story (embeds the PWA via `embed.FS`), honoring "super-easy self-host." Cost: a 3rd language alongside the C# plugin and JS PWA. If team fluency is decisively C#, **.NET** is the defensible one-language fallback (self-contained single-file publish, heavier artifacts). Node/TS matches SponsorBlock but ships a runtime + node_modules per deploy. **Pending the team-fluency call.**
- *Sources:* `tech-stack-and-devops.md` Open Qs.

### Q5 — Overload Jellyfin's segment-type enum vs. carry an external taxonomy
Jellyfin's `MediaSegmentType` enum is a fixed 6 values (Intro/Outro/Recap/Preview/Commercial/Annotation) — no content-filter categories.
- **Lean: external + translate at emit.** Carry cleanyfin's rich 9-category taxonomy in the federated DB; map to the nearest Jellyfin type (e.g. Annotation/Commercial) only at provider emit time, so the crowdsourced model isn't crippled by a 6-value enum. Track upstream request #3396 for a dedicated filter type + mute action.
- *Sources:* `jellyfin-integration-mechanics.md`, `prior-art-and-oss-competitors.md` Open Qs.

### Q7 — How to identify distinct CUTS safely (theatrical/extended/director's/TV edit)
Auto-matching the wrong cut silently mis-times filters — a trust-breaker for a family-safety tool.
- **Lean:** Explicit `release` rows per cut, matched primarily by **runtime bucket (±2s) + optional chapter fingerprint**, on top of moviehash + duration (R04). When match confidence is low, **fail safe** — prompt the user to confirm the cut rather than silently applying possibly-wrong timings. Rely on votes/confidence to surface bad matches, not automated frame-fingerprinting in v1.
- *Sources:* `tagging-taxonomy-and-data-model.md`, `prior-art-and-oss-competitors.md`, `federation-architecture.md` Open Qs.

## Product / values (decide before public launch)

### Q6 — Severity: single ordinal (0–3) vs. independent sub-flags
VidAngel uses independent sub-filters; ClearPlay uses an ordinal ladder.
- **Lean:** Ordinal **0–3 per category** for the default one-slider UX, **plus** optional boolean sub-tags per segment for advanced filtering (e.g. profanity `{mild, strong, sexual, blasphemy, discriminatory}`). ClearPlay simplicity by default, VidAngel granularity when needed — without two conflicting models. Blasphemy-as-flag-vs-severity remains a genuine modeling debate.
- *Sources:* `tagging-taxonomy-and-data-model.md` Open Qs.

### Q8 — Jellyfin / "-fin" trademark & naming check
"cleanyfin" leans on the Jellyfin brand and the community "-fin" suffix convention.
- **Lean:** Proactively email **team@jellyfin.org** for a FLOSS naming/branding blessing — the policy invites it, and official-ecosystem status is worth far more than the effort, ideally *before* the name is embedded in installs and manifests. Otherwise rely on the general third-party allowance.
- *Sources:* `legal-and-ip-landscape.md` Open Qs.

## Secondary leans (noted, low-urgency)

| # | Question | Current lean |
|---|---|---|
| S1 | Do read-only mirrors ever accept upstream submissions? | v1 mirrors stay read-only; contributions go to the hub. Upstream-via-signed-Git-bundles is the federation-upgrade phase (R03/R07), not now. |
| S2 | Sybil/rate-limit defense depth (account-free identity)? | IP rate limits + vote-score auto-hide + shadowbans for v1 (SponsorBlock's proven set). Reserve proof-of-work / curator-weighting for if abuse actually appears. |
| S3 | Litestream in the default compose, or opt-in? | Opt-in. Default = SQLite file on a volume + nightly local `.backup` cron; Litestream documented as the one-step off-box upgrade. |
| S4 | Minimum Jellyfin ABI — net8.0 (10.10) or net9.0 (10.11)? | Primary = current stable 10.11.x / net9.0; add a 10.10.x/net8.0 manifest entry only on demand. |
| S5 | PWA framework — SvelteKit vs. htmx/Alpine vs. React? | SvelteKit (adapter-static) for a real app UI, or htmx if the marking flow stays simple. Both static-export into the Go binary. |
| S6 | Auto-mute aggressiveness — whole-cue vs. word-level? | Whole-cue for `auto_suggested` (safe, over-mutes); human reviewers tighten to word-level on confirmation (R10). |
| S7 | Do we ever touch DRM-protected commercial streams? | No — user-owned Jellyfin library files only. Filtering commercial streams pulls in §1201/TOS risk and breaks R01's clean scope. |
| S8 | Lightweight FTO review of ClearPlay's post-2015 patents? | Get a cheap targeted look at US9762963 / US10313744 / US11750887 before any funded promotion or donations; foundational patents are expired. |
| S9 | EDL written next to media vs. served via API/sidecar? | Serve edit-decisions via the segment API by default; generate `.edl` on demand only for opt-in Kodi/mpv users, avoiding a hard writeable-library dependency. |
| S10 | Cross-border liability where no Family Movie Act equivalent (EU/UK)? | Ship per-jurisdiction docs; keep nodes independently operated so no single entity aggregates global liability. |

See also: [`31-TRADEOFFS`](./31-TRADEOFFS.md) (accepted tensions), [`20-ROADMAP`](./20-ROADMAP.md) (spike exit criteria), [`23-CONTRIBUTION-WORKFLOWS`](./23-CONTRIBUTION-WORKFLOWS.md).
