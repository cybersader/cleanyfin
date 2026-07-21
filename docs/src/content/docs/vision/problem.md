---
title: The Problem
description: Why safe, per-profile content filtering on a self-hosted Jellyfin library isn't solved yet, and the precise legal line cleanyfin stays behind.
sidebar:
  order: 1
---

## The want

People run their own Jellyfin server and want to watch mainstream movies and shows **without the objectionable parts** — profanity, sex/nudity, graphic violence, gore — gated **per viewer profile** (the kids get one filter, the adults get another, with a "request a bypass" escape hatch). This is the VidAngel/ClearPlay *experience*, brought to a self-hosted library.

## Why it isn't solved yet

Two walls, and cleanyfin exists to get past both.

1. **The OSS options are weak.** There is exactly one Jellyfin-specific content-filter plugin (`jacob-willden/jellyfin-plugin-moviecontentfilter`) and it is self-described as "very early development," single dev, no releases, and — critically — **no crowdsourcing, no moderation, no federation, no in-player marking**. It consumes local `.mcf` files. Adjacent tools each solve one slice (SponsorBlock = crowdsourcing but for YouTube ads; cleanvid = profanity-only, subtitle-driven; PlexAutoSkip = Plex-only, no shared DB). Nobody ships the *combination*: a real crowdsourced+moderated segment database, self-host/federation, native per-profile Jellyfin enforcement, and frictionless marking. That gap is the opening (see [Existing Work](/cleanyfin/project/existing-work/), [Prior Art](/cleanyfin/project/prior-art/)).
2. **The legal history is scary.** Anyone who has looked at this space knows VidAngel got hit with a **~$62.4M jury verdict** (settled down to $9.9M, Chapter 11). That verdict scares builders off the whole category. But the verdict was about *how* VidAngel operated, not about filtering itself — and the difference is the entire ballgame.

## The legal line — precisely

The **Family Movie Act** (17 U.S.C. §110(11), enacted 2005-04-27, Pub. L. 109-9) is a statutory safe harbor. It exempts from infringement the **real-time "making imperceptible"** (skip or mute) of *limited portions* of a motion picture, during private home viewing, **from an authorized copy**, **provided no fixed copy of the altered version is created**. It explicitly blesses *providing the enabling technology*, not just end-user use. Four load-bearing conditions:

| Condition | Meaning for cleanyfin |
|---|---|
| "making imperceptible" only | Skip/mute allowed; **adding** replacement audio/video is not. |
| "authorized copy" | Source must be the user's own lawfully obtained file. |
| "private home viewing" | Household use, not redistribution. |
| "no fixed copy" of the edit | **Never** export/bake a cleaned MP4. Filter live, in the player. |

**ClearPlay (legal, 20+ years):** ships a timeline edit-list; the user's own player skips/mutes the user's own copy. No A/V redistributed. Never enjoined. This is the exact architecture cleanyfin copies.

**VidAngel (lost):** it *decrypted DRM* on discs (DMCA §1201 anti-circumvention — strict liability, independent of infringement) and *streamed its own decrypted copies*. The FMA did not save it, because the copies were not "authorized" and it created fixed intermediate copies. Two things the FMA does not cover — and the two things cleanyfin must never do.

Two more shields from the deep-dive: timestamp+category lists are **uncopyrightable facts** (Feist; the basis SponsorBlock has run on openly since 2019), and user contributions are covered by **DMCA §512** notice-and-takedown hygiene (registered agent + moderation).

## What cleanyfin solves — and the constraint it's born under

**cleanyfin is the crowdsourced segment *layer* that makes a self-hosted Jellyfin library safe to watch per profile** — the missing "shared database + native enforcement + easy marking" that no OSS project delivers together.

It can only exist by staying on the ClearPlay side of the line. That is **R01, the legal keystone**: metadata only, never media. The federated database contains **only** `title/version identifier + in-point + out-point + category + moderation metadata`. cleanyfin never hosts, caches, transcodes, proxies, exports, or decrypts a single frame of A/V, and never performs or scripts DRM circumvention — the filtering happens in real time, in the user's own player, against the user's own file. (R01; see [Tradeoffs](/cleanyfin/project/tradeoffs/), [Principles](/cleanyfin/vision/principles/).)

**Honest scope limits (not glossed):**
- The FMA is **US-only** — no EU/UK equivalent — so a federated design must be jurisdiction-aware; node operators bear their own local risk (legal deep-dive Finding 10).
- Native Jellyfin clients have **no mute action yet** (as of 10.11, mid-2026): the MVP is **skip-only** on Web + Android TV, with EDL export giving true mute on Kodi/mpv. The word-level "mute the profanity, keep the scene" that VidAngel is loved for is upstream-gated — cleanyfin must not overpromise it (R07; [Jellyfin integration research](/cleanyfin/research/jellyfin/)).

## Sources

- Family Movie Act text — <https://www.copyright.gov/legislation/pl109-9.html>
- VidAngel verdict — <https://variety.com/2019/biz/news/vidangel-jury-verdict-damages-1203245947>
- ClearPlay model — <https://en.wikipedia.org/wiki/ClearPlay>
- DMCA §1201 — <https://www.copyright.gov/1201/> · Feist basis — <https://github.com/ajayyy/SponsorBlock/wiki/Database-and-API-License>
