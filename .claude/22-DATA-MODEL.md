# cleanyfin Data Model — The Keystone

> 🌳 Substantive content (not a thin pointer). This is cleanyfin's differentiator — the "translation layer" that makes a crowdsourced timestamp portable across everyone's different rips. Distilled from [`../knowledge-base/01-working/tagging-taxonomy-and-data-model.md`](../knowledge-base/01-working/tagging-taxonomy-and-data-model.md) and [`../knowledge-base/01-working/federation-architecture.md`](../knowledge-base/01-working/federation-architecture.md). Primitives are defined in [`./03-CONCEPTS.md`](./03-CONCEPTS.md); this file gives the concrete schema. Decisions: R04–R06, R08, R09.

> **Correction (2026-07-21, Spike B → R14):** the *shipped* Jellyfin `MediaSegmentDto` carries only `Id, ItemId, Type, StartTicks, EndTicks` — **no `Action`, `StreamIndex`, or `Comment`** (those were in the design proposal, not the release). So cleanyfin's rich fields (**severity, action, category, votes, provenance**) live **entirely in cleanyfin's own DB** (below); when emitting to Jellyfin we set only `Type` + tick span, and the current provider emits Unknown while discarding submitted action/category. A metadata proxy cannot execute actions; pinned Web v10.11.11 defaults Unknown to None. Native Action.Mute/Skip fields and generic Kodi/mpv EDL equivalence are rejected. Dated reconciliation: 2026-10-08; see the [reviewed proposal](../knowledge-base/01-working/long-term-architecture-2026-10-07/04-architecture-proposal.md), which remains PROPOSED.

## Overview — what this covers

**PROPOSED portability goal:** reuse annotations only after an explicitly verified asset-to-timeline binding. It is not established for arbitrary copies/cuts. The following three-layer model and offset diagram are historical design sketches, not implemented calibration:

1. **`title`** — the abstract work (TMDB/IMDb id, name, year). Metadata only.
2. **`release`** — one specific encode/cut of that title, identified by a content **fingerprint**. Segments are keyed here.
3. **local file** — the household's actual copy, resolved to a `release` plus a per-file **calibration offset**. This layer lives on the client, not in the shared DB.

Times are stored **once, canonically, as integer milliseconds** and translated to Jellyfin ticks or EDL seconds only at the export boundary — with proposed checked numeric bounds and conversion; integer storage alone does not prove arithmetic or mapping correctness.

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

**Historical calibration hypotheses (R04), not solved matching — correction 2026-10-08:**

Runtime buckets, duration and sampled ends only locate candidates; they cannot prove equivalent cuts. Offsets require distributed matching anchors, drift a tested rate mapping, and edits bounded piecewise mappings. Do not extrapolate or over-filter guessed boundaries. Wrong/unknown bindings require refusal or explicitly authorized unfiltered playback. All tiers below are PROPOSED/UNVERIFIED.

| Tier | Method | Cost | When it runs |
|---|---|---|---|
| 1 | **Candidate lookup only** — title/runtime/hash cannot verify the right timeline | unmeasured | explicit binding validation required |
| 2 | **User offset** — one adjustable `calibration_offset_ms` slider per file | trivial | when tier-1 is close but shifted a few seconds |
| 3 | **Chromaprint audio anchor** (opt-in v2) — fingerprint a short region near a known segment, locate it locally, derive the offset automatically | adds fpcalc/FFmpeg dep | opt-in accelerator only |

**Fail-safe on low confidence (R04):** if fingerprint + duration don't confidently resolve to a release, cleanyfin surfaces *"no verified data for this exact file"* and prefers over-filtering or a confirmation prompt over silently applying possibly-wrong timings. A missed mute in a family-safety tool is a trust-breaker. Note tiers 1–2 only correct a **fixed** offset; progressive drift (23.976 vs 25 fps PAL) needs ffsubsync-style alignment, out of scope for v1.

## Historical normalized SQL sketch — PROPOSED, not current schema or an approved migration

The sketch's published default, global vote precedence and claimed submitter IDs are not accepted safety contracts. A UUID is not a content digest. Proposed publication requires explicit authority acceptance; curation binds origin/annotation ID, reviewed content revision/digest and timeline-binding revision. Mutation preserves historical decisions but re-evaluates applicability; local-copy behavior after deletion/reappearance is owner-controlled.

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

## Implementation status (2026-07-23)

The SQL above is the *target* normalized model. What ships on `main` after Phase 3 slices 1–4 is a deliberately flattened subset:

- **Live — real moviehash (`osh:`).** The fingerprint is the OpenSubtitles **moviehash** the plugin computes per file (`osh:` + filesize/first+last-64 KiB; `jf:<ItemId>` fallback when the bytes can't be read), replacing the earlier `jf:ItemId` placeholder (R04). The PWA resolves the *same* fingerprint via the plugin's `GET /Cleanyfin/Fingerprint`.
- **IMPLEMENTED prefix lookup:** 4–16 hex chars of SHA-256(fingerprint), returning full matching fingerprints; no minimum anonymity set or exact-title secrecy is guaranteed. The plugin uses exact lookup.
- **Live but flattened.** Segments live in a single `segment` table keyed **directly on the `fingerprint` string** (plus a `vote` table) — not yet split into `title`/`release`. `duration_ms` is carried inline on the segment instead of via a `release` FK.
- **IMPLEMENTED dump:** visible-only records, not raw votes/hidden state or a private backup. No importer, replication, private overlays or curator/profile resolver is implemented.
- **Publication:** new rows are pending but exact/prefix/dump reads include non-hidden rows with votes > -2; pending is not quarantine. Submitter strings are unauthenticated claims.
- **PROPOSED:** normalized work/asset/timeline binding, revision-bound curation, private policy and checked `0 <= start < end <= verified duration`; unknown duration is unresolved. Current lookup checks fingerprint only, not duration.

## Historical normalized JSON example — PROPOSED, not query/dump wire reality

Current segment fields are `id`, `fingerprint`, `durationMs`, `startMs`, `endMs`, `category`, `severity`, `action`, `submitterId`, `votes`, `status`, `createdAt`. Spans/duration are milliseconds; `createdAt` is Unix seconds (`store.go:26–38,139–144`). Exact reads wrap `fingerprint` and `segments`; dump wraps `generatedAtUnix`, `count`, `segments`. The normalized example below is not an API payload or authorship/consent record.

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
| Current Jellyfin provider | milliseconds × 10000; bounds not yet established | Unknown plus tick span; submitted actions discarded |
| Proposed player-specific exports | adapter-defined units/timeline | Kodi and mpv need distinct formats and action tests; no generic equivalence |

PROPOSED: unsupported actions require explicit authorized substitution or refusal; no silent mute-to-skip conversion. The current provider does not enforce this contract.

## Limitations / Trade-offs (honest)

- **moviehash is a speed hash** — collides on same-size/same-ends files and breaks on re-mux, so exact-file coverage can be sparse. Runtime buckets only widen candidate lookup; neither they nor an audio fingerprint prove cross-cut timeline equivalence.
- **Distinct cuts genuinely need distinct `release` rows and distinct segments.** Auto-matching the wrong cut silently mis-times filters — hence the fail-safe prompt.
- **A single global offset can't fix progressive drift** (framerate mismatch), only a fixed shift.
- **PROPOSED suggestion-only gate:** explicit human acceptance must govern curated views. Current non-hidden/vote-threshold queries do not enforce published-only reads (R10).

See open modeling debates (blur/crop, severity-vs-sub-flags, fingerprint choice) in [`./40-QUESTIONS-OPEN.md`](./40-QUESTIONS-OPEN.md); how these tables are populated and moderated in [`./23-CONTRIBUTION-WORKFLOWS.md`](./23-CONTRIBUTION-WORKFLOWS.md).
