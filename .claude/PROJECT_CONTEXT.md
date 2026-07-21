# cleanyfin — Project Context

> First locked: 2026-07-21 (initialization + research fan-out). If a numbered stub disagrees with this file, this file wins until reconciled. When a decision locks in a session, propagate it here + to `FOCUS.md` + `41-QUESTIONS-RESOLVED.md` in the same session.

## What This Project Is

**cleanyfin is an open-source, self-hosted content-filtering layer for Jellyfin, backed by a federated, crowdsourced database of tagged content segments.** VidAngel-style *experience* (skip/mute the stuff you don't want — profanity, violence, nudity, etc., gated per viewer profile) built on a SponsorBlock-style *data model* (community-submitted, voted, moderated timestamps that anyone can mirror).

One phrase, three pillars:
- **Crowdsourced** — the value is a shared database of tagged segments people build together, with voting + moderation, not a single curator's list. SponsorBlock is the architectural template.
- **Federated (subsidiarity)** — people share their databases; a household/community node works fully offline and opt-in publishes/pulls from wider community data. No single central authority is required. Different communities can filter differently (subscribable **curator profiles**) rather than fighting over one global truth.
- **DMCA-safe & free** — cleanyfin distributes **only** timestamps + category metadata + edit-decisions, applied in real time to the user's *own* copy in their *own* player. It never hosts, caches, transcodes, proxies, exports, or decrypts a single frame of A/V. This is the exact line ClearPlay stayed behind and VidAngel crossed.

**Category word: "layer" / "filter."** Not "a VidAngel clone," not "a media server." It's the crowdsourced segment layer that makes Jellyfin safer to watch.

## The Architecture in One Line

A thin **Jellyfin plugin** (C#/.NET `IMediaSegmentProvider`) pulls community-tagged segments from a **small self-hostable API server** (the SponsorBlock-clone hub) and emits them as native Jellyfin Media Segments so clients skip them; a **companion marking PWA** lets people stamp in/out points while they watch and submit them back. **The server + its open dataset are the product; the plugin and PWA are thin clients.**

## Hard Constraints (violating these = wrong direction)

1. **Metadata only, never media.** Ship timestamps + categories + edit-decisions (EDL/MediaSegments), applied to media the user already possesses. Never host/transcode/redistribute/decrypt A/V; never bundle clips or screenshots "for reference." This is the legal keystone (Family Movie Act / ClearPlay; see `01-PROBLEM`, `04-PRIOR-ART`, `31-TRADEOFFS`).
2. **Super-easy setup + resilient DevOps is a feature, not an afterthought.** The headline install is one `docker compose up` (or a single static binary + systemd). Backup = copy a file. It must not fall over. A non-expert self-hosts in ~5 minutes. (Maintainer said "super easy, and I mean super easy.")
3. **Simplify first.** Bigger side project, not ultra-serious. Prefer boring, proven, low-maintenance tech. Defer distributed-systems machinery (ActivityPub/nostr/matrix/shared-DB CRDTs) — mirrors + public dumps *are* the v1 federation.
4. **No forced accounts.** Contribution safety = pseudonymous submitter IDs + moderation queue + voting + curator locks, never an account wall. (Same value as the sibling projects.)
5. **No hyperscalers / no k8s.** Self-hosting preferred; single-node resilience via `restart: unless-stopped` + healthcheck + file/Litestream backup. No AWS/GCP/Kubernetes.
6. **Build on upstream, don't fork it.** Register as a standard Jellyfin Media Segments provider; ride native skip UI. Track upstream mute-action progress rather than hacking playback.

## Who It's For

- **Families / households** who want to watch mainstream content on their own Jellyfin server with objectionable parts skipped, gated per kid's profile, with a "request a bypass" escape hatch.
- **Contributors** who'll mark a bad segment in three taps while watching, without signing up.
- **Curators** whose filtering standards others choose to follow (subsidiarity: pick the community whose norms match yours).
- **Self-hosters** who want a one-command install that just works and backs up by copying a file.
- **Developers & AI agents** maintaining the DB and the plugin through the same reviewed, metadata-only pipeline.
- **User (Cybersader):** self-hoster, Obsidian power user, WSL-on-Windows, collaborates with Claude Code across sibling projects (cyberbaser, cynario, retake-studio…).

## Where Knowledge Lives (knowledge-ops map)

- **`knowledge-base/01-working/`** — the current **canonical depth**: six research deep-dives (legal-and-ip, prior-art, jellyfin-integration, federation, tech-stack, taxonomy) with citations, from the 2026-07-21 fan-out. Research goes INTO files here, not into chat.
- **`.claude/` numbered stubs** — this orientation layer: greppable summaries + pointers into the deep-dives. Read `PROJECT_CONTEXT` → `FOCUS`, then follow stubs.
- **`docs/` (planned)** — an Astro + Starlight docs site (the sibling-project convention) that will become the public, canonical KB and the thing contributors rally around. Not built yet (see `20-ROADMAP`).
- **`RESEARCH_SOURCES.md`** — curated primary sources.

## Relationship to Sibling Projects

Shared `.claude/` + `knowledge-base/` + Astro-Starlight-docs + portagenty/Tailscale convention across Cybersader projects:
- **cyberbaser** — the layout this project copies (numbered stubs → docs site).
- **cynario**, **retake-studio** — same convention; portagenty session patterns (docs / share-app / tests) mirrored in `cleanyfin.portagenty.toml`.
