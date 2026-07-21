<h1 align="center">cleanyfin</h1>

<p align="center">
  <b>An open-source, self-hosted content-filtering layer for <a href="https://jellyfin.org">Jellyfin</a>,<br>
  backed by a federated, crowdsourced database of tagged content segments.</b>
</p>

<p align="center">
  <i>The VidAngel experience. The SponsorBlock data model. DMCA-safe by design.</i>
</p>

---

> **Status: Phase 0 — research & knowledge-ops initialized (2026-07-21).** No code yet, on purpose. This repo currently holds the project's *brain*: a researched, cited knowledge base that anyone can read to understand exactly what we're building and why. Code follows two feasibility spikes and one licensing decision (see [`.claude/20-ROADMAP.md`](.claude/20-ROADMAP.md)). **Contributors welcome — start with the knowledge base below.**

## The idea

Watching mainstream movies and shows on your own Jellyfin server should not mean taking whatever's in them. cleanyfin lets a household **skip or mute** the parts it doesn't want — profanity, violence, nudity, and more — gated **per viewer profile**, with a per-title **"request a bypass"** escape hatch. The filtering data is **crowdsourced**: people tag segments (in/out timestamps + category) while they watch, vote on each other's tags, and **share** those databases. No single central authority — communities can **federate** and follow the **curators** whose standards they share (subsidiarity).

Think **[ClearPlay](https://www.clearplay.com/)/VidAngel for the experience**, **[SponsorBlock](https://sponsor.ajay.app/) for the data**, and **free + self-hosted** for everything.

## The one rule that makes it legal

**cleanyfin distributes only timestamps + category metadata + edit-decisions — never a single frame of audio or video, and it never touches DRM.** Filters are applied in real time to the copy *you already own*, in *your own* player. This is the exact line U.S. law drew: the [Family Movie Act of 2005](https://www.copyright.gov/legislation/pl109-9.html) legalizes real-time "making imperceptible" of an authorized copy with no fixed edited copy — which is why ClearPlay is legal, and why VidAngel lost (it made copies and broke DRM). SponsorBlock has run this exact posture for years. See [`.claude/01-PROBLEM.md`](.claude/01-PROBLEM.md) and [`knowledge-base/01-working/legal-and-ip-landscape.md`](knowledge-base/01-working/legal-and-ip-landscape.md).

## How it will work

```
   ┌─────────────────────┐   pulls community      ┌──────────────────────────┐
   │  Jellyfin plugin     │◄──  segments  ─────────│  cleanyfin server (Go)    │
   │  (thin C# provider)  │                        │  • crowdsourced segment DB│
   │  emits Media Segments│                        │  • submit / vote / moderate│
   │  → native skip UI    │                        │  • public dumps + mirrors  │
   └─────────────────────┘                        │  • SQLite (one file)       │
                                                    └──────────────────────────┘
   ┌─────────────────────┐   marks in/out +              ▲  one `docker compose up`
   │  companion PWA       │──  category, submits  ────────┘  (or a single binary)
   │  reads Jellyfin      │
   │  playback position   │
   └─────────────────────┘
```

- **Build on Jellyfin's native [Media Segments](https://jellyfin.org/docs/general/server/metadata/media-segments/)** (10.10+) — the same mechanism the Intro Skipper plugin uses — so clients render skip buttons for free.
- **Super-easy to self-host** is a hard requirement: the headline install is one `docker compose up`; backup is copying a file; it's built to not fall over. No hyperscalers, no Kubernetes.
- **No forced accounts.** Contribution safety = pseudonymous IDs + voting + moderation queue, never a signup wall.

Full architecture: [`.claude/21-ARCHITECTURE.md`](.claude/21-ARCHITECTURE.md) · data model: [`.claude/22-DATA-MODEL.md`](.claude/22-DATA-MODEL.md).

## Read the knowledge base

This project is documented before it's built. Start here:

| Read first | Then browse |
|---|---|
| [`.claude/PROJECT_CONTEXT.md`](.claude/PROJECT_CONTEXT.md) — what this is | [`.claude/00-INDEX.md`](.claude/00-INDEX.md) — the full map |
| [`.claude/FOCUS.md`](.claude/FOCUS.md) — where it's at | [`.claude/41-QUESTIONS-RESOLVED.md`](.claude/41-QUESTIONS-RESOLVED.md) — decisions + why |
| [`.claude/20-ROADMAP.md`](.claude/20-ROADMAP.md) — what's next | [`knowledge-base/01-working/`](knowledge-base/01-working/) — 6 cited research deep-dives |

## Prior art we build on (and interoperate with)

[SponsorBlock](https://github.com/ajayyy/SponsorBlockServer) (the crowdsourcing model) · [Intro Skipper](https://github.com/intro-skipper/intro-skipper) & [segment-editor](https://github.com/intro-skipper/segment-editor) (Jellyfin segment provider + in-player marking) · [MovieContentFilter](https://github.com/delight-im/MovieContentFilter) (the open `.mcf` standard we interoperate with) · [Kodi EDL](https://kodi.wiki/view/Edit_decision_list) (portable edit-decisions, real mute) · [cleanvid/monkeyplug](https://github.com/mmguero/cleanvid) (subtitle/speech profanity automation). See [`.claude/04-PRIOR-ART.md`](.claude/04-PRIOR-ART.md).

## Contributing

The best contribution right now is **reading the knowledge base and poking holes in it** — open an issue against any decision in [`41-QUESTIONS-RESOLVED.md`](.claude/41-QUESTIONS-RESOLVED.md) or weigh in on the [open questions](.claude/40-QUESTIONS-OPEN.md). When code starts, it'll be a Go segment server, a C# Jellyfin plugin, and a PWA — thin slices, one `docker compose up`.

## Licensing

- **Code** (server, Jellyfin plugin, companion app, tooling): **AGPL-3.0-or-later** — see [`LICENSE`](LICENSE).
- **Dataset** (the crowdsourced content segments): **CC0-1.0** (public domain dedication) — see [`DATA-LICENSE`](DATA-LICENSE).

Rationale: timestamp/category data is factual (thin copyright), so CC0 maximizes federation, mirroring, and reuse and avoids the non-commercial ambiguity that constrains other filter datasets; copyleft give-back lives on the AGPL server code where it's enforceable. Consequence: cleanyfin does **not** ingest CC-BY-NC-SA data (SponsorBlock/MCF) — it interoperates with the `.mcf`/EDL *formats* and bootstraps via automated subtitle analysis + original contributions. Only contribute data you can place under CC0. Decision logged as R15 in [`.claude/41-QUESTIONS-RESOLVED.md`](.claude/41-QUESTIONS-RESOLVED.md).

---

<p align="center"><sub>cleanyfin is not affiliated with Jellyfin, ClearPlay, VidAngel, or SponsorBlock. It filters media you already own; it never distributes copyrighted content.</sub></p>
