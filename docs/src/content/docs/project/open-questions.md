---
title: Open Questions
description: The genuinely-unresolved calls a maintainer must still make, each with a research-backed current lean.
sidebar:
  order: 5
---

:::note[Update 2026-07-21]
The two Spike questions are now **resolved** — enforcement ([Spike A](/cleanyfin/research/spike-a-enforcement/) → R13) and the segment write API ([Spike B](/cleanyfin/research/spike-b-segment-write-api/) → R14). The **only remaining hard gate before code is the data license.**
:::

> 🌳 **Live here** — this is real content, not a pointer stub. These are the genuinely-unresolved calls gating real code, synthesized from every deep-dive's "Open Questions" plus the two feasibility spikes in [Roadmap](/cleanyfin/project/roadmap/). Settled decisions live in [Decisions (resolved)](/cleanyfin/project/decisions/) (R01–R12); when one of these locks, move it there and update FOCUS.md + PROJECT_CONTEXT.md in the same session.
>
> Snapshot date: 2026-07-21. Each item carries a **current lean** — a research-backed default, not a commitment.

## Blocking (decide before writing production code / seeding data)

### Q2 — DATA license: CC0/CC-BY vs. CC-BY-NC-SA — **decide before seeding**
The single most time-sensitive call. It is effectively **irreversible** for a crowdsourced DB and it **conflicts with seed data**: MCF and SponsorBlock both publish under **CC BY-NC-SA 4.0**, so importing their data to solve cold-start (R11) would force the whole dataset share-alike + non-commercial.
- **Lean:** CC0 (or CC BY 4.0) for the timestamp/label *facts* — maximally reusable, federation-friendly, matches the "free and DMCA-safe" ethos and avoids NC/share-alike friction across nodes. **But** that means we likely *cannot* ingest CC-BY-NC-SA seed data — pick the license first, then decide what we're allowed to seed from. Confirm NC-vs-not deliberately. (Code license lean: **AGPL-3.0**, like SponsorBlock.)
- *Sources:* [legal-and-ip-landscape](/cleanyfin/research/legal/), [prior-art-and-oss-competitors](/cleanyfin/research/prior-art-oss/), [federation-architecture](/cleanyfin/research/federation/) Open Qs.

### Q3 — Enforcement spike: does the plugin enforce per-profile server-side? (**Spike A**)
Media Segments are global per library item; there may be no server-side hook that forces a kid's client to honor the filter. This determines whether the plugin is the **enforcement point** or just a settings/EDL/segment *provider*.
- **Lean:** Run a small spike against Jellyfin 10.11 before committing architecture. If server-side per-profile enforcement is limited, fall back to a client-cooperative EDL/segment-delivery model (SponsorBlock-style) and be honest about the trust boundary — see [Trade-offs](/cleanyfin/project/tradeoffs/) #6. A true per-user layer becomes a fast-follow, not an MVP blocker.
- *Sources:* [tech-stack-and-devops](/cleanyfin/research/tech-stack/), [jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/) Open Qs.

### Q4 — Exact 10.11+ segment write API (**Spike B**)
The Media Segments API was absorbed into Intro Skipper. The companion PWA needs the current create/delete route + payload.
- **Lean:** Inspect Intro Skipper's 10.11 source and the OpenAPI spec at api.jellyfin.org directly; assume a POST taking `ItemId, Type, StartTicks, EndTicks (, StreamIndex)` requiring an admin/API token. Confirm before building the PWA write path.
- *Sources:* [jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/) Open Qs.

## Structural (shape the architecture; decide before/at skeleton)

### Q1 — Primary server language: Go vs. .NET vs. Node/TS
- **Lean: Go** — best single-static-binary/deploy + resilience story (embeds the PWA via `embed.FS`), honoring "super-easy self-host." Cost: a 3rd language alongside the C# plugin and JS PWA. If team fluency is decisively C#, **.NET** is the defensible one-language fallback (self-contained single-file publish, heavier artifacts). Node/TS matches SponsorBlock but ships a runtime + node_modules per deploy. **Pending the team-fluency call.**
- *Sources:* [tech-stack-and-devops](/cleanyfin/research/tech-stack/) Open Qs.

### Q5 — Overload Jellyfin's segment-type enum vs. carry an external taxonomy
Jellyfin's `MediaSegmentType` enum is a fixed 6 values (Intro/Outro/Recap/Preview/Commercial/Annotation) — no content-filter categories.
- **Lean: external + translate at emit.** Carry cleanyfin's rich 9-category taxonomy in the federated DB; map to the nearest Jellyfin type (e.g. Annotation/Commercial) only at provider emit time, so the crowdsourced model isn't crippled by a 6-value enum. Track upstream request #3396 for a dedicated filter type + mute action.
- *Sources:* [jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/), [prior-art-and-oss-competitors](/cleanyfin/research/prior-art-oss/) Open Qs.

### Q7 — How to identify distinct CUTS safely (theatrical/extended/director's/TV edit)
Auto-matching the wrong cut silently mis-times filters — a trust-breaker for a family-safety tool.
- **Lean:** Explicit `release` rows per cut, matched primarily by **runtime bucket (±2s) + optional chapter fingerprint**, on top of moviehash + duration (R04). When match confidence is low, **fail safe** — prompt the user to confirm the cut rather than silently applying possibly-wrong timings. Rely on votes/confidence to surface bad matches, not automated frame-fingerprinting in v1.
- *Sources:* [tagging-taxonomy-and-data-model](/cleanyfin/research/taxonomy/), [prior-art-and-oss-competitors](/cleanyfin/research/prior-art-oss/), [federation-architecture](/cleanyfin/research/federation/) Open Qs.

## Product / values (decide before public launch)

### Q6 — Severity: single ordinal (0–3) vs. independent sub-flags
VidAngel uses independent sub-filters; ClearPlay uses an ordinal ladder.
- **Lean:** Ordinal **0–3 per category** for the default one-slider UX, **plus** optional boolean sub-tags per segment for advanced filtering (e.g. profanity `{mild, strong, sexual, blasphemy, discriminatory}`). ClearPlay simplicity by default, VidAngel granularity when needed — without two conflicting models. Blasphemy-as-flag-vs-severity remains a genuine modeling debate.
- *Sources:* [tagging-taxonomy-and-data-model](/cleanyfin/research/taxonomy/) Open Qs.

### Q8 — Jellyfin / "-fin" trademark & naming check
"cleanyfin" leans on the Jellyfin brand and the community "-fin" suffix convention.
- **Lean:** Proactively email **team@jellyfin.org** for a FLOSS naming/branding blessing — the policy invites it, and official-ecosystem status is worth far more than the effort, ideally *before* the name is embedded in installs and manifests. Otherwise rely on the general third-party allowance.
- *Sources:* [legal-and-ip-landscape](/cleanyfin/research/legal/) Open Qs.

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

See also: [Trade-offs](/cleanyfin/project/tradeoffs/) (accepted tensions), [Roadmap](/cleanyfin/project/roadmap/) (spike exit criteria), [Contribution Workflows](/cleanyfin/design/contribution-workflows/).
