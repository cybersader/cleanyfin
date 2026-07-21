---
title: Data Model — The Keystone
description: The translation layer that makes one crowdsourced timestamp portable across everyone's different rips — release keying, 3-tier calibration, and the concrete schema.
sidebar:
  order: 3
---

:::caution[Correction (2026-07-21, Spike B)]
The *shipped* Jellyfin `MediaSegmentDto` carries only `Id, ItemId, Type, StartTicks, EndTicks` — **no `Action`, `StreamIndex`, or `Comment`** (those were in the design proposal, not the release). cleanyfin's rich fields (severity, action, category, votes, provenance) live entirely in cleanyfin's own DB; when emitting to Jellyfin we set only `Type` + tick span, and the action is resolved by the client setting, cleanyfin's [response-filtering proxy](/cleanyfin/research/spike-a-enforcement/), or [EDL](/cleanyfin/research/spike-b-segment-write-api/). Read any "→ Jellyfin `Action.Mute`" mapping below in that light.
:::

> Substantive content (not a thin pointer). This is cleanyfin's differentiator — the "translation layer" that makes a crowdsourced timestamp portable across everyone's different rips. Distilled from [taxonomy & data model](/cleanyfin/research/taxonomy/) and [federation architecture](/cleanyfin/research/federation/). Primitives are defined in [Concepts](/cleanyfin/design/concepts/); this file gives the concrete schema. Decisions: R04–R06, R08, R09.

## Overview — what this covers

The whole point of the project is that **one person tags a scene once and it works on everyone's copy of the film.** That only holds if segment timing is decoupled from any physical file. The model does this in three layers:

1. **`title`** — the abstract work (TMDB/IMDb id, name, year). Metadata only.
2. **`release`** — one specific encode/cut of that title, identified by a content **fingerprint**. Segments are keyed here.
3. **local file** — the household's actual copy, resolved to a `release` plus a per-file **calibration offset**. This layer lives on the client, not in the shared DB.

Times are stored **once, canonically, as integer milliseconds** and translated to Jellyfin ticks or EDL seconds only at the export boundary — so float drift and rounding bugs never enter the DB.

## How It Works — release keying + 3-tier calibration (R04)

```
 shared DB (release-relative ms)          client / plugin (per-file)
 ┌───────────────────────────┐            ┌────────────────────────────────┐
 │ segment.start_ms = 723000 │            │ local file → fingerprint match │
 │ keyed to release #R        │──fetch──▶ │ resolve to release #R          │
 └───────────────────────────┘            │ + calibration_offset_ms = −480 │
                                          │ effective = 723000 − 480       │
                                          │           = 722520 ms          │
                                          └──────────────┬─────────────────┘
                                                         ▼  convert at export
                          Jellyfin ticks = ms × 10000  |  EDL = ms / 1000.0 (float sec)
```

**Calibration is solved in three escalating tiers — cheapest first (R04):**

| Tier | Method | Cost | When it runs |
|---|---|---|---|
| 1 | **Identity match** — title id + runtime bucket (±2s) → the right `release` | ~free | always; the common popular-encode case just works |
| 2 | **User offset** — one adjustable `calibration_offset_ms` slider per file | trivial | when tier-1 is close but shifted a few seconds |
| 3 | **Chromaprint audio anchor** (opt-in v2) — fingerprint a short region near a known segment, locate it locally, derive the offset automatically | adds fpcalc/FFmpeg dep | opt-in accelerator only |

**Fail-safe on low confidence (R04):** if fingerprint + duration don't confidently resolve to a release, cleanyfin surfaces *"no verified data for this exact file"* and prefers over-filtering or a confirmation prompt over silently applying possibly-wrong timings. A missed mute in a family-safety tool is a trust-breaker. Note tiers 1–2 only correct a **fixed** offset; progressive drift (23.976 vs 25 fps PAL) needs ffsubsync-style alignment, out of scope for v1.

## Implementation — SQL sketch

```sql
-- The abstract work. Metadata only, never media.
CREATE TABLE title (
  id            INTEGER PRIMARY KEY,
  tmdb_id       TEXT,                 -- external ids, either may be null
  imdb_id       TEXT,
  name          TEXT NOT NULL,
  year          INTEGER,
  season        INTEGER,              -- null for movies
  episode       INTEGER
);

-- One specific encode/cut. Segments key to THIS, not to a file path.
CREATE TABLE release (
  id            INTEGER PRIMARY KEY,
  title_id      INTEGER NOT NULL REFERENCES title(id),
  moviehash     TEXT,                 -- OpenSubtitles OSHash (filesize + first/last 64KB)
  runtime_ms    INTEGER NOT NULL,     -- exact duration; secondary match key + staleness signal
  cut_label     TEXT,                 -- 'theatrical' | 'extended' | 'tv_edit' | ...
  container     TEXT,
  chapter_count INTEGER,
  UNIQUE (title_id, moviehash, runtime_ms)
);

-- THE segment: a tagged in/out span. SponsorBlock-shaped, Jellyfin-compatible.
CREATE TABLE segment (
  uuid          TEXT PRIMARY KEY,               -- stable, content-addressable (sign-ready, R07-fed)
  release_id    INTEGER NOT NULL REFERENCES release(id),
  start_ms      INTEGER NOT NULL,               -- release-relative, canonical unit
  end_ms        INTEGER NOT NULL,
  category      TEXT NOT NULL,                  -- fixed-9 enum (R05)
  severity      INTEGER NOT NULL DEFAULT 1,     -- 0..3 ordinal ladder
  action        TEXT NOT NULL DEFAULT 'skip',   -- mute|skip|mark  (blur|crop reserved -> skip)
  tags          TEXT,                           -- free-form long-tail, JSON array
  submitter_id  TEXT NOT NULL,                  -- pseudonymous hashed id (R08)
  curator_id    INTEGER REFERENCES curator(id), -- null = community-submitted
  votes         INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'published', -- auto_suggested|published|hidden
  locked        INTEGER NOT NULL DEFAULT 0,     -- curator lock wins over unlocked (R09)
  src_duration_ms INTEGER,                      -- duration at submit time (staleness check)
  created_at    INTEGER NOT NULL,
  CHECK (severity BETWEEN 0 AND 3),
  CHECK (end_ms > start_ms)
);

-- One vote per (segment, submitter). Score <= -2 auto-hides (R08).
CREATE TABLE vote (
  segment_uuid  TEXT NOT NULL REFERENCES segment(uuid),
  submitter_id  TEXT NOT NULL,
  value         INTEGER NOT NULL,     -- +1 up / -1 down
  created_at    INTEGER NOT NULL,
  PRIMARY KEY (segment_uuid, submitter_id)
);

-- Subscribable trust circle (R09). Precedence: curator-locked > community-voted > unmoderated.
CREATE TABLE curator (
  id            INTEGER PRIMARY KEY,
  handle        TEXT UNIQUE NOT NULL,
  pubkey        TEXT,                 -- optional: signs dump bundles for Git-federation upgrade
  blessed       INTEGER NOT NULL DEFAULT 0
);

-- A household viewing profile = per-category max severity + optional action override.
CREATE TABLE filter_profile (
  id            INTEGER PRIMARY KEY,
  jellyfin_user TEXT,                 -- map 1:1 onto a Jellyfin user where possible
  parent_id     INTEGER REFERENCES filter_profile(id),  -- inheritance; child only MORE strict
  rules_json    TEXT NOT NULL         -- {category: {max_severity, action_override}}
);

CREATE INDEX idx_segment_release ON segment(release_id, status);
CREATE INDEX idx_release_match   ON release(title_id, runtime_ms);
```

## Implementation — a segment as JSON (query/dump wire shape)

```json
{
  "uuid": "9f2c1a7e-4b3d-4e21-8c6a-0d5e7f9a1b2c",
  "release": {
    "title": { "name": "Example Film", "year": 2019, "tmdb_id": "512195" },
    "moviehash": "8e245d9679d31e12",
    "runtime_ms": 7412000,
    "cut_label": "theatrical"
  },
  "start_ms": 723000,
  "end_ms": 729500,
  "category": "profanity",
  "severity": 2,
  "action": "mute",
  "tags": ["f-word"],
  "submitter_id": "sb_3af91c",
  "curator_id": null,
  "votes": 14,
  "status": "published",
  "locked": false,
  "src_duration_ms": 7412000,
  "created_at": 1753056000000
}
```

**Export translation (done at the boundary, never stored):**

| Target | start | action mapping |
|---|---|---|
| Jellyfin `MediaSegment` | `StartTicks = start_ms × 10000` | `mute → Action.Mute`, `skip → Action.Skip`, `mark → Action.None` (Type = Annotation) |
| Kodi/mpv `.edl` | `start_ms / 1000.0` (float sec) | `mute → 1`, `skip → 3` (or `0`), `mark → 2` |

If a client ignores `Action` and only skips, the export layer degrades **predictably** — a muted-dialogue segment falls back to `skip` only with explicit user consent, since skipping muted dialogue removes plot (R06 caveat).

## Limitations / Trade-offs (honest)

- **moviehash is a speed hash** — collides on same-size/same-ends files and breaks on re-mux, so exact-file coverage can be sparse. Runtime-bucket identity (tier 1) widens it; Chromaprint (tier 3, v2) generalizes across rips.
- **Distinct cuts genuinely need distinct `release` rows and distinct segments.** Auto-matching the wrong cut silently mis-times filters — hence the fail-safe prompt.
- **A single global offset can't fix progressive drift** (framerate mismatch), only a fixed shift.
- **`auto_suggested` segments are never trusted** until a human confirms (R10) — the data model enforces the quality gate, it doesn't replace it.

See open modeling debates (blur/crop, severity-vs-sub-flags, fingerprint choice) in [Open Questions](/cleanyfin/project/open-questions/); how these tables are populated and moderated in [Contribution Workflows](/cleanyfin/design/contribution-workflows/).
