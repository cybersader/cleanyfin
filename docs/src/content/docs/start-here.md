---
title: Start here
description: A five-minute orientation to cleanyfin — what it is, where it's at, and where to read next.
sidebar:
  order: 0
---

**cleanyfin is an open-source, self-hosted content-filtering layer for Jellyfin, backed by a federated, crowdsourced database of tagged content segments.** VidAngel *experience*, SponsorBlock *data model*, DMCA-safe by construction, free and self-hosted.

## The 60-second version

- **What it does:** skip/mute objectionable content (profanity, violence, nudity, …) on your own Jellyfin server, gated **per viewer profile**, with a per-title **request-bypass**.
- **How the data works:** people **crowdsource** tagged segments (in/out timestamps + category) while watching; the community votes and moderates; anyone can **mirror** the database. No central authority — communities **federate** and follow **curators** whose standards they share.
- **Why it's legal:** cleanyfin ships **only** timestamps + metadata, applied to media you already own, in your own player. Never a frame of A/V, never DRM. This is the [Family Movie Act / ClearPlay](/cleanyfin/research/legal/) side of the line VidAngel crossed.
- **Why it's easy:** the headline install is one `docker compose up`; backup is copying a file. Super-easy self-host is a hard requirement, not a nicety.

## The status right now

**Phase 0 — research complete, no code yet (on purpose).** This site is the project's researched, cited knowledge base. Two feasibility spikes and one licensing decision gate the first code. See the [roadmap](/cleanyfin/project/roadmap/).

## Where to read next

| If you want… | Read |
|---|---|
| The why + the legal line | [The problem](/cleanyfin/vision/problem/) |
| The full picture | [Vision](/cleanyfin/vision/vision/) · [Principles](/cleanyfin/vision/principles/) |
| How it's built | [Architecture](/cleanyfin/design/architecture/) · [Data model](/cleanyfin/design/data-model/) · [Concepts](/cleanyfin/design/concepts/) |
| What's been tried | [Prior art](/cleanyfin/project/prior-art/) · [The competitor](/cleanyfin/project/existing-work/) |
| What's next + the honest tensions | [Roadmap](/cleanyfin/project/roadmap/) · [Trade-offs](/cleanyfin/project/tradeoffs/) |
| The decisions + what's still open | [Decisions](/cleanyfin/project/decisions/) · [Open questions](/cleanyfin/project/open-questions/) |
| The primary research | [Research deep dives](/cleanyfin/research/legal/) |

## How to help

The best contribution today is **reading and poking holes.** Open an issue against any [decision](/cleanyfin/project/decisions/) or weigh in on an [open question](/cleanyfin/project/open-questions/) — especially the **data-license** call and the **enforcement** feasibility question, which gate real code.
