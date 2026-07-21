# cleanyfin Concepts — The Primitives

> 📎 Pointer stub. Plain-language glossary of the nouns the whole system is built from. Backed by the deep-dives [`../knowledge-base/01-working/tagging-taxonomy-and-data-model.md`](../knowledge-base/01-working/tagging-taxonomy-and-data-model.md) and [`../knowledge-base/01-working/federation-architecture.md`](../knowledge-base/01-working/federation-architecture.md). For the concrete schema see [`./22-DATA-MODEL.md`](./22-DATA-MODEL.md); for the decision log see [`./41-QUESTIONS-RESOLVED.md`](./41-QUESTIONS-RESOLVED.md).

These are the load-bearing terms. Everything else (plugin, PWA, hub) is machinery that moves these objects around. Read top to bottom — each builds on the one above.

```
TITLE ──has many──▶ RELEASE ──identified by──▶ FINGERPRINT (+ CALIBRATION OFFSET per local file)
                       │
                       └──has many──▶ SEGMENT ──carries──▶ CATEGORY + SEVERITY + ACTION
                                          │                        ▲
   SUBMITTER creates ┘  CURATOR locks ┘   │                        │
                                          └── PROFILE resolves what actually happens at playback
                                                     └── BYPASS temporarily lifts a filter
```

## SEGMENT — the core primitive

A **segment** is one tagged in/out span on a specific release: *"from 00:12:03 to 00:12:29 there is strong violence."* It is a row of metadata — start/end times, a category, a severity, a default action, plus crowdsourcing fields (submitter, votes, status, locked) — and **never any audio or video**. That metadata-only shape is the legal keystone (R01): cleanyfin ships timestamps, the user's own player does the muting/skipping. Modeled after SponsorBlock's segment fused with Jellyfin's native `MediaSegment`. Times are stored canonically as **integer milliseconds** and converted to Jellyfin ticks or EDL float-seconds only at export (see [`./22-DATA-MODEL.md`](./22-DATA-MODEL.md)).

## CATEGORY + SEVERITY — what kind, how much (R05)

Every segment names exactly one **category** from a **fixed set of 9** (closed for v1 so federated data stays consistent; a free-form `tags` field absorbs the long tail):

`profanity` · `sexual_dialogue` · `sex_scene` · `nudity` · `violence` · `gore` · `disturbing` · `substance_use` · `crude`

Each also carries an ordinal **severity 0–3**, mirroring ClearPlay's ladder: `0` none/absent · `1` mild/implied · `2` strong/explicit · `3` extreme/graphic. Severity is what lets ~9 sliders replace VidAngel's ~80 toggles — the "super-easy" win. A viewer's profile sets a *max-allowed* severity per category; any segment above that line gets filtered.

## ACTION — what the player does (R06, R07)

The **action** enum says how a filtered segment is handled: `mute` (silence audio, keep the picture and plot), `skip` (jump past it), or `mark` (leave it, just flag/awareness). `blur` and `crop` are **schema-reserved but not implemented** in v1 — they need real-time video processing, so they render as `skip` with a visible notice. Each segment stores a *default* action (default map: profanity/sexual_dialogue/crude → mute; sex_scene/nudity/violence/gore/disturbing → skip; substance_use → mark), but the **profile resolves the actual action at playback** — Jellyfin's own "clients decide the action" design, so one shared segment serves households with different tastes. Reality check (R07): native Jellyfin clients only skip today, so the MVP is **skip-only on Web + Android TV**; real mute ships via **EDL export** for Kodi/mpv until upstream mute lands.

## RELEASE + FINGERPRINT + CALIBRATION OFFSET — matching the right file (R04)

A timestamp is worthless against the wrong rip. A **release** is one specific encode/cut of a title (theatrical vs. extended vs. TV edit — genuinely different segment sets). It is identified by a **fingerprint** = OpenSubtitles-style **moviehash** (filesize + first/last 64 KB, fast on multi-GB files) **plus exact runtime/duration** (the same signal SponsorBlock's `videoDuration` uses to detect a changed video). Segments are keyed to `(title + release fingerprint)`, never to a raw path. Each local file then resolves to a release plus a small **calibration offset** (`calibration_offset_ms`) that nudges every segment to line up with that particular copy. When match confidence is low, cleanyfin **fails safe** — prompt or over-filter rather than silently mis-time a family-safety filter.

## PROFILE — who is watching, and the BYPASS state machine

A **profile** (mapped 1:1 onto a Jellyfin user where possible, reusing existing auth/parental controls) holds, per category, a **max severity** and optional **action override**. Profiles **inherit**: a household sets a default filter profile; each child inherits and may only be made *more* restrictive unless an admin loosens it. The **bypass** escape hatch is a small state machine so a blocked title is never a dead end:

```
REQUESTED ──▶ APPROVED{ scope: title|segment|category, expires_at }
          └─▶ DENIED
APPROVED  ──▶ EXPIRED   (approvals are per-title and time-boxed)
```

v1 keeps this simple: an admin toggles the exception in the dashboard with an expiry; push-notification approve/deny is deferred.

## CURATOR / trust circle — whose standards you follow (R09)

A **curator** is a trusted contributor whose segment set others *subscribe* to — the subsidiarity mechanism. Rather than standing up separate servers per community, conflicting norms **coexist inside one open dataset** as competing/overlapping segment sets, resolved by a clear precedence rule: **subscribed-curator-locked > community-voted > unmoderated**. A curator can **lock** a segment (analogous to SponsorBlock's VIP lock) so it wins over unlocked competitors. Provenance is shown per applied edit so "why was this skipped?" always has an answer.

## SUBMITTER — who tagged it, without an account (R08)

A **submitter** is the pseudonymous author of a segment. No account wall: a locally-generated UUID is hashed into a public submitter ID, and lookups use a **k-anonymity hash-prefix** so the hub never learns exactly which title a household is watching. Abuse resistance comes from voting (auto-hide at score ≤ −2), shadowbans for vandals, and curator locks — not from forced sign-up. Automation output (subtitle/word-list profanity, AI classification) enters as `status='auto_suggested'` and needs a human before it becomes `published` (R10).

---

**How they fit:** a `SUBMITTER` tags a `SEGMENT` (with `CATEGORY`+`SEVERITY`+`ACTION`) on a `RELEASE` identified by a `FINGERPRINT`; a household's `PROFILE` and its subscribed `CURATOR`s decide which segments apply and what the player does, with `BYPASS` as the time-boxed override. The concrete tables and calibration tiers live in [`./22-DATA-MODEL.md`](./22-DATA-MODEL.md); how segments get created and moderated lives in [`./23-CONTRIBUTION-WORKFLOWS.md`](./23-CONTRIBUTION-WORKFLOWS.md); the honest tensions (skip-vs-mute, granularity, matching) in [`./31-TRADEOFFS.md`](./31-TRADEOFFS.md).
