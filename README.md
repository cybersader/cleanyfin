<div align="center">

<img src="assets/logo-card.svg" alt="cleanyfin logo" width="128" height="128" />

# cleanyfin

### Watch what you *want* to watch.

An open-source, self-hosted **content-filtering layer for [Jellyfin](https://jellyfin.org)** — skip or mute the parts you don't want (profanity, violence, nudity, and more), gated **per viewer profile**, powered by a **federated, crowdsourced database of tagged segments**.

**The VidAngel experience · the SponsorBlock data model · DMCA-safe by design.**

[![docs](https://github.com/cybersader/cleanyfin/actions/workflows/deploy-docs.yml/badge.svg)](https://cybersader.github.io/cleanyfin/)
[![server CI](https://github.com/cybersader/cleanyfin/actions/workflows/server-ci.yml/badge.svg)](https://github.com/cybersader/cleanyfin/actions/workflows/server-ci.yml)
[![plugin CI](https://github.com/cybersader/cleanyfin/actions/workflows/plugin-ci.yml/badge.svg)](https://github.com/cybersader/cleanyfin/actions/workflows/plugin-ci.yml)
[![Jellyfin 10.11+](https://img.shields.io/badge/Jellyfin-10.11%2B-00A4DC?logo=jellyfin&logoColor=white)](https://jellyfin.org)
[![code: AGPL-3.0](https://img.shields.io/badge/code-AGPL--3.0-blue.svg)](LICENSE)
[![data: CC0-1.0](https://img.shields.io/badge/data-CC0--1.0-lightgrey.svg)](DATA-LICENSE)

[Documentation](https://cybersader.github.io/cleanyfin/) · [How it works](#how-it-works) · [Quick start](#quick-start) · [What's shipped](#whats-shipped) · [Roadmap](.claude/20-ROADMAP.md) · [Contributing](#contributing)

</div>

---

> **🛠️ Building in the open.** The full pipeline — the crowdsourced **segment API**, the **Jellyfin plugin**, and the **marking app** — is scaffolded, tested, and CI-green ([what's shipped](#whats-shipped)). Not yet a one-click install; contributors and Jellyfin tinkerers welcome.

## Why

Watching mainstream movies and shows on your own Jellyfin server shouldn't mean taking whatever's in them. cleanyfin lets a household **skip or mute** the objectionable parts, gated **per viewer profile**, with a per-title **"request a bypass"** escape hatch — the [ClearPlay](https://www.clearplay.com/)/VidAngel idea, but **free, self-hosted, and community-owned**.

The filtering data is **crowdsourced**: people tag segments (in/out timestamps + category) while they watch, the community votes and moderates, and anyone can **mirror** the database. No single central authority — communities **federate** and follow the **curators** whose standards they share.

Think **ClearPlay/VidAngel for the experience**, **[SponsorBlock](https://sponsor.ajay.app/) for the data**, and **your Jellyfin server for the home**.

## The one rule that makes it legal

cleanyfin distributes **only timestamps + category metadata + edit-decisions — never a single frame of audio or video, and it never touches DRM.** Filters are applied in real time to the copy *you already own*, in *your own* player. That's the exact line U.S. law drew in the [Family Movie Act of 2005](https://www.copyright.gov/legislation/pl109-9.html): why ClearPlay is legal, why VidAngel (which made copies and broke DRM) lost, and why SponsorBlock has run this way for years. → [Legal deep-dive](https://cybersader.github.io/cleanyfin/research/legal/)

## How it works

```
   ┌──────────────────────┐   marks in/out + category      ┌────────────────────────────┐
   │  Marking app (PWA)    │──  POST /api/v1/segments  ────>│  cleanyfin API server (Go)  │
   │  reads live playback  │                                │  • crowdsourced segment DB  │
   │  position + fingerprint│<─  fingerprint (moviehash) ───│  • submit / vote / moderate │
   └──────────────────────┘                                │  • k-anon lookup · public   │
                                                            │    dumps for mirrors        │
   ┌──────────────────────┐   GET /api/v1/segments?fp=      │  • SQLite (one file)        │
   │  Jellyfin plugin (C#) │──  by release fingerprint  ───>│  one `docker compose up`    │
   │  IMediaSegmentProvider│<─  matching segments  ─────────└────────────────────────────┘
   │  → native skip in     │
   │  Web / Android TV / … │
   └──────────────────────┘
```

- **Built on Jellyfin's native [Media Segments](https://jellyfin.org/docs/general/server/metadata/media-segments/)** (10.10+) — the same mechanism the Intro Skipper plugin uses — so clients render skip buttons natively.
- **Segments key on a [moviehash](https://cybersader.github.io/cleanyfin/research/legal/) fingerprint**, so a tag someone made lines up on *your* copy of the same rip.
- **Super-easy to self-host** is a hard requirement: the server is one `docker compose up`; backup is copying a file. No hyperscalers, no Kubernetes.
- **No forced accounts.** Contribution safety = pseudonymous IDs + voting + moderation, with **k-anonymity** so the server never learns which title you're watching.

Full design: [Architecture](https://cybersader.github.io/cleanyfin/design/architecture/) · [Data model](https://cybersader.github.io/cleanyfin/design/data-model/).

## Quick start

The **segment API** (the hub) runs today:

```bash
git clone https://github.com/cybersader/cleanyfin && cd cleanyfin
docker compose up -d --build        # http://localhost:8080
curl localhost:8080/healthz          # -> ok
```

The **Jellyfin plugin** (`plugin/`) and **marking PWA** (`pwa/`) build cleanly and talk to that API — see their READMEs. End-to-end install against a live Jellyfin server is the next milestone.

## What's shipped

| Component | What it does | Status |
|---|---|---|
| **`server/`** — Go API | Crowdsourced segment DB (SQLite/WAL): submit · vote (auto-hide ≤ −2) · exact + **k-anonymity** lookup · public **dump** for mirrors · CORS. One static binary, one `docker compose up`. | ✅ built · tested · CI |
| **`plugin/`** — C# Jellyfin plugin | `IMediaSegmentProvider` that fetches by **moviehash** fingerprint and emits native Media Segments; a fingerprint + write endpoint. Jellyfin 10.11 / .NET 9. | ✅ builds · CI |
| **`pwa/`** — marking app | Vite + TS: reads live `/Sessions` position, stamps in/out + category, submits to the API. | ✅ builds · CI |
| **`docs/`** — docs site | Astro + Starlight, published to GitHub Pages. | ✅ live |

The project is documented *before* it's finished — the whole design + research lives in [`.claude/`](.claude/00-INDEX.md) (orientation) and [`knowledge-base/`](knowledge-base/) (cited deep-dives + the decision log).

## Built on / interoperates with

[SponsorBlock](https://github.com/ajayyy/SponsorBlockServer) (the crowdsourcing model) · [Intro Skipper](https://github.com/intro-skipper/intro-skipper) & [segment-editor](https://github.com/intro-skipper/segment-editor) (Jellyfin segment provider + in-player marking) · [MovieContentFilter](https://github.com/delight-im/MovieContentFilter) (the open `.mcf` format) · [Kodi EDL](https://kodi.wiki/view/Edit_decision_list) (portable edit-decisions) · [OpenSubtitles moviehash](https://opensubtitles.github.io/oshash/) (the fingerprint). See [prior art](https://cybersader.github.io/cleanyfin/project/prior-art/).

## Contributing

Best contribution right now: **read the [knowledge base](https://cybersader.github.io/cleanyfin/) and poke holes in it** — open an issue against any [decision](.claude/41-QUESTIONS-RESOLVED.md) or [open question](.claude/40-QUESTIONS-OPEN.md). Code-wise, the stack is a Go server, a C# Jellyfin plugin, and a PWA — thin slices, each CI-gated. **Only contribute segment data you can place under CC0** (see below).

## Licensing

- **Code** (server, plugin, app, tooling): **AGPL-3.0-or-later** — see [`LICENSE`](LICENSE).
- **Dataset** (the crowdsourced content segments): **CC0-1.0** public-domain dedication — see [`DATA-LICENSE`](DATA-LICENSE).

Timestamp/category data is factual (thin copyright), so CC0 maximizes federation and reuse; copyleft give-back lives on the AGPL server code where it's enforceable. (Rationale: R15 in the [decision log](.claude/41-QUESTIONS-RESOLVED.md).)

---

<div align="center">
<sub>cleanyfin is not affiliated with Jellyfin, ClearPlay, VidAngel, or SponsorBlock. It filters media you already own — it never hosts, transcodes, or distributes copyrighted content.</sub>
</div>
