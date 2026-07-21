# cleanyfin Knowledge Base — Index

> **How this KB works (layers):** the current **canonical depth** is `knowledge-base/01-working/` — six cited research deep-dives from the 2026-07-21 fan-out. This `.claude/` layer is the **orientation + pointer layer** for humans and agents: read `PROJECT_CONTEXT.md` then `FOCUS.md` for direction, then follow the numbered stubs into the deep-dives. A future `docs/` (Astro + Starlight) site will become the public canonical KB (see `20-ROADMAP`). **Locked a decision? Update this layer + `FOCUS.md` + `41-QUESTIONS-RESOLVED.md` in the same session.**

## Meta (read first, in order)

| File | What it's for |
|---|---|
| [PROJECT_CONTEXT.md](./PROJECT_CONTEXT.md) | Locked identity, hard constraints, knowledge-ops map, siblings |
| [FOCUS.md](./FOCUS.md) | Current state, what's locked, what's next (dated snapshot) |
| [KNOWLEDGE_BASE_PHILOSOPHY.md](./KNOWLEDGE_BASE_PHILOSOPHY.md) | The living-KB pattern shared across Cybersader projects |
| [DOCUMENTATION_STYLE.md](./DOCUMENTATION_STYLE.md) | Naming, structure, tone conventions |
| [RESEARCH_SOURCES.md](./RESEARCH_SOURCES.md) | Curated primary sources (real content, not a stub) |

## Numbered stubs

| Stub | Covers | Backing deep-dive(s) in `knowledge-base/01-working/` |
|---|---|---|
| [01-PROBLEM](./01-PROBLEM.md) | Why cleanyfin exists; the legal line it must stay behind | `legal-and-ip-landscape.md` |
| [02-ECOSYSTEM](./02-ECOSYSTEM.md) | Jellyfin, SponsorBlock, ClearPlay/VidAngel, the players around us | `prior-art-*`, `jellyfin-*` |
| [03-CONCEPTS](./03-CONCEPTS.md) | The primitives: segment, release, fingerprint, profile, curator, action | `tagging-taxonomy-*`, `federation-*` |
| [04-PRIOR-ART](./04-PRIOR-ART.md) | What's been tried (SponsorBlock, Intro Skipper, MCF, cleanvid, EDL…) | `prior-art-and-oss-competitors.md` |
| [05-EXISTING-WORK](./05-EXISTING-WORK.md) | The direct competitor(s) and cleanyfin's opening | `prior-art-and-oss-competitors.md` |
| [10-VISION-SHORT](./10-VISION-SHORT.md) / [11-VISION-LONG](./11-VISION-LONG.md) | Where this goes, short + long | all |
| [12-PRINCIPLES](./12-PRINCIPLES.md) | The rules every design decision is checked against | all |
| [20-ROADMAP](./20-ROADMAP.md) | Phases, the two feasibility spikes, exit criteria | all |
| [21-ARCHITECTURE](./21-ARCHITECTURE.md) | Plugin + server + PWA; how the pieces fit | `jellyfin-*`, `tech-stack-*`, `federation-*` |
| [22-DATA-MODEL](./22-DATA-MODEL.md) | The keystone: segment/release schema + version calibration | `tagging-taxonomy-*`, `federation-*` |
| [23-CONTRIBUTION-WORKFLOWS](./23-CONTRIBUTION-WORKFLOWS.md) | Marking, voting, moderation, curators, automation gate | `federation-*`, `tagging-taxonomy-*` |
| [31-TRADEOFFS](./31-TRADEOFFS.md) | Honest tensions (skip-vs-mute, granularity, federation cost, matching) | all |
| [40-QUESTIONS-OPEN](./40-QUESTIONS-OPEN.md) | Unresolved decisions a maintainer must make | all |
| [41-QUESTIONS-RESOLVED](./41-QUESTIONS-RESOLVED.md) | **Live here** — the decision log (R01…) with rationale | — |

## Status Legend
- 🌳 **Live here** — real content in this layer (PROJECT_CONTEXT, FOCUS, 20-ROADMAP, 40/41, RESEARCH_SOURCES).
- 📎 **Pointer stub** — summary + link into the canonical `knowledge-base/01-working/` deep-dive.

**Feasibility spikes** (2026-07-21, in `knowledge-base/01-working/`): `spike-a-enforcement.md` (→ R13), `spike-b-segment-write-api.md` (→ R14), `spike-c-client-support.md` (→ R07). Docs site is live under `docs/` (Astro + Starlight).

Current state (2026-07-21): Phase 1 nearly closed — research + spikes done, docs site stood up; the sole gate before code is the **data-license** decision (Q2). See `FOCUS.md`.
