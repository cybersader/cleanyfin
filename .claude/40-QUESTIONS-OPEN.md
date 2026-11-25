# Open Questions — decisions a maintainer must still make

> 🌳 **Live here** — this is real content, not a pointer stub. These are the genuinely-unresolved calls gating real code, synthesized from every deep-dive's "Open Questions" plus the two feasibility spikes in [`20-ROADMAP`](./20-ROADMAP.md). Settled decisions live in [`41-QUESTIONS-RESOLVED`](./41-QUESTIONS-RESOLVED.md) (R01–R12); when one of these locks, move it there and update [`FOCUS.md`](./FOCUS.md) + [`PROJECT_CONTEXT.md`](./PROJECT_CONTEXT.md) in the same session.
>
> Correction date: 2026-10-08. July leans remain historical, not commitments. Owner-approved sequencing: dependable filtering on explicitly supported cooperative clients first; server-side enforcement/deliberate-bypass resistance retained LONGER TERM, not a first-release prerequisite. See the dated owner decision in [41-QUESTIONS-RESOLVED](./41-QUESTIONS-RESOLVED.md). The rest of the [reviewed roadmap](../knowledge-base/01-working/long-term-architecture-2026-10-07/05-roadmap-and-experiments.md) remains PROPOSED. Still-open owner gates: first tuple/actions and exposure tolerance; failure/stale/unfiltered/active-revocation rules; trust/privacy, legacy publication, revision-sensitive curation and public provenance; authorized exception scope; recovery/trust-reset and weekly maintainer budgets; actual enforcement scope/design. Playback correctness remains UNVERIFIED.

## Resolved gates (was: Blocking)

> Historical spikes permitted initial code; they did not clear production protection gates. R15 remains the recorded license selection, not legal/import clearance. Q3/runtime compatibility are reopened by source counterevidence; isolated metadata experiments can proceed only under their own authorization.

### Q2 — DATA license — ✅ RESOLVED 2026-07-21 (→ R15)
**Decision: `CC0-1.0` for the dataset + `AGPL-3.0-or-later` for the code.** Data is factual (thin copyright); CC0 maximizes federation/mirror/reuse and kills the NC ambiguity; give-back protection sits on the AGPL server code. *Accepted consequence:* cannot bulk-ingest CC-BY-NC-SA data (SponsorBlock/MCF) — cold-start via automated subtitle generation + original contributions; interoperate with the `.mcf`/EDL **formats** only (R11). Files: `LICENSE`, `DATA-LICENSE`. Verify any third-party seed set's license (e.g. VideoSkip) before importing.

### Q3 — Playback scope and enforcement — sequencing SETTLED; design/runtime OPEN 2026-10-08
Owner chose supported-client cooperative filtering for the first release, retaining server-side enforcement/bypass resistance longer term. Provider generation is user-blind; a proxy selects metadata, not mandatory actions or original-media access. Metadata filtering/remote commands alone are not unbypassable enforcement. Later design must define the threat model and account for alternate clients, downloads, file shares and authorization; no enforceability against a server administrator or someone controlling media is claimed. Technique, compatibility/cost, reliable failure/recovery and applicable legal review remain gates, not approved designs. Web v10.11.11 defaults Unknown to None; first client/actions, exposure tolerance, failure policy, selected-source/profile behavior, timing and ordinary/forced refresh preservation remain open live gates. The conditional same-manager delayed-response hypothesis also needs E1/E5; no runtime defect or exploit is asserted.

### Q4 — Exact 10.11+ segment write API — ✅ RESOLVED 2026-07-21 (Spike B → R14)
**Answer (verified):** core Jellyfin has **no** segment write endpoint; the community route was folded into Intro Skipper and coupled to its DB. Decision (R14): PWA writes to cleanyfin's Go API; plugin materializes segments and hosts its own thin write controller; don't depend on Intro Skipper's route. **Correction:** shipped `MediaSegmentDto` = `Id, ItemId, Type, StartTicks, EndTicks` only (no `StreamIndex`/`Action`/`Comment`). See `spike-b-segment-write-api.md`.

## Structural (shape the architecture; decide before/at skeleton)

### Q1 — Primary server language: Go vs. .NET vs. Node/TS
- **Lean: Go** — best single-static-binary/deploy + resilience story (embeds the PWA via `embed.FS`), honoring "super-easy self-host." Cost: a 3rd language alongside the C# plugin and JS PWA. If team fluency is decisively C#, **.NET** is the defensible one-language fallback (self-contained single-file publish, heavier artifacts). Node/TS matches SponsorBlock but ships a runtime + node_modules per deploy. **Pending the team-fluency call.**
- *Sources:* `tech-stack-and-devops.md` Open Qs.

### Q5 — Overload Jellyfin's segment-type enum vs. carry an external taxonomy
There is no native rich content-filter action/category contract here. Current provider emits Unknown and discards action/category. Proposed: retain external taxonomy; do not silently relabel intervals as Intro/Commercial/Annotation to borrow unrelated client settings. A tested adapter and explicit capability/refusal contract remain open.
- *Sources:* `jellyfin-integration-mechanics.md`, `prior-art-and-oss-competitors.md` Open Qs.

### Q7 — How to identify distinct CUTS safely (theatrical/extended/director's/TV edit)
Auto-matching the wrong cut silently mis-times filters — a trust-breaker for a family-safety tool.
- **PROPOSED:** explicit asset-to-timeline bindings, selected source/audio identity and reviewed revision. Runtime buckets/hash/duration locate candidates only; current lookup is fingerprint-only. Do not apply guessed timings. Distributed anchors/rate/piecewise mappings require tests; preserved approval must not silently transfer to mutated content.
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
| S2 | Account-free abuse/publication? | Current claimed identities are unauthenticated and pending rows public. Proposed credentials establish possession, not unique humans; legacy transition and curator authority need owner choices. |
| S3 | Recovery tier and backup mechanism? | Proposed completed consistent SQLite backup, preserved WAL, coordinated keys/config and verified off-device restore. Public dump is not backup; async replication is not failover or a fixed RPO. Measure age of last usable recovery point. |
| S4 | Supported ABI tuple? | Compiled net9.0/Jellyfin.Controller 10.11.11 only; loading/playback and other versions remain unverified. |
| S5 | PWA framework — SvelteKit vs. htmx/Alpine vs. React? | SvelteKit (adapter-static) for a real app UI, or htmx if the marking flow stays simple. Both static-export into the Go binary. |
| S6 | Auto-mute aggressiveness — whole-cue vs. word-level? | Whole-cue for `auto_suggested` (safe, over-mutes); human reviewers tighten to word-level on confirmation (R10). |
| S7 | Do we ever touch DRM-protected commercial streams? | No — user-owned Jellyfin library files only. Filtering commercial streams pulls in §1201/TOS risk and breaks R01's clean scope. |
| S8 | Lightweight FTO review of ClearPlay's post-2015 patents? | Get a cheap targeted look at US9762963 / US10313744 / US11750887 before any funded promotion or donations; foundational patents are expired. |
| S9 | Player-specific exports/sidecars? | Proposed separate tested Kodi/mpv adapters; format equivalence and mute compatibility remain unverified. No mandatory writable-library mount. |
| S10 | Cross-border liability where no Family Movie Act equivalent (EU/UK)? | Ship per-jurisdiction docs; keep nodes independently operated so no single entity aggregates global liability. |

See also: [`31-TRADEOFFS`](./31-TRADEOFFS.md) (accepted tensions), [`20-ROADMAP`](./20-ROADMAP.md) (spike exit criteria), [`23-CONTRIBUTION-WORKFLOWS`](./23-CONTRIBUTION-WORKFLOWS.md).
