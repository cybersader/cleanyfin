# Contribution Workflows — how data gets made and kept clean

> 📎 Pointer stub. Backed by [`../knowledge-base/01-working/federation-architecture.md`](../knowledge-base/01-working/federation-architecture.md) and [`../knowledge-base/01-working/tagging-taxonomy-and-data-model.md`](../knowledge-base/01-working/tagging-taxonomy-and-data-model.md). Siblings: [`22-DATA-MODEL`](./22-DATA-MODEL.md) (the schema being written), [`21-ARCHITECTURE`](./21-ARCHITECTURE.md) (the three components), [`31-TRADEOFFS`](./31-TRADEOFFS.md), [`03-CONCEPTS`](./03-CONCEPTS.md).

The value of cleanyfin is the **shared, moderated dataset of tagged segments** — not any one client. This stub covers how a segment goes from "someone noticed something" to "trusted, published, mirrorable." The whole pipeline is SponsorBlock's proven recipe (R08–R10), specialized to a family-safety tool where a *missed* filter breaks trust.

## The marking flow (companion PWA)

The marking client is the thin, static PWA embedded in the Go server binary (R05 stack). A contributor watches their own copy and stamps segments in three taps — **no account, ever** (R08, hard constraint #4).

```
watch own file → tap IN → tap OUT → pick category + severity → (optional action) → Submit
        │                                                                 │
        │  PWA computes the release fingerprint of the local file          ▼
        │  (OpenSubtitles moviehash + exact duration — R04)      POST /api/segments
        ▼                                                        {fingerprint, start_ms,
   offline? → queue in a local outbox table, sync when online     end_ms, category, severity,
   (CRDT/outbox lives ONLY in the client — federation R6/R8)       action, submitter_id}
```

- Times are captured and stored as **integer milliseconds, release-relative**; converted to Jellyfin ticks (×10000) or EDL seconds only at export (R06, taxonomy R6).
- The submission is keyed to `(title_ref, release fingerprint)`, never a raw filename — that portability is the keystone (R04, [`22-DATA-MODEL`](./22-DATA-MODEL.md)).
- **Metadata only, never media (R01).** The PWA transmits timestamps + category + edit-decision. It never uploads a clip, a frame, a screenshot, an audio snippet, or a subtitle line "for reference." This is a *contribution-level* constraint, not just a distribution one — the submit endpoint has no media field to populate. It is the legal keystone (Family Movie Act / ClearPlay side of the line; see [`01-PROBLEM`](./01-PROBLEM.md)).

## The moderation model (R08)

No gates — reputation and voting, mirroring SponsorBlock at millions of segments.

| Mechanism | Rule | Why |
|---|---|---|
| Pseudonymous ID | Local UUID hashed 5000× (SHA-256) → public `submitter_id`. No email/account. | Reputation without PII (hard constraint #4). |
| Voting | `POST /api/vote` up/down, or a category-reclassification vote. | Community self-correction. |
| Auto-hide | Segment score **≤ −2** → hidden from everyone (still retained in DB). | Cheap vandalism resistance. |
| Shadowban | A flagged vandal's segments stay visible only to their originating IP; silently hidden from all others; auto-applies to future submits. | Removes the feedback loop that motivates vandals. |
| Curator locks | A curator-locked segment always wins over unlocked ones; removable only by curators. | Stops churn once a segment is timed perfectly. |
| Exposure | Weighted-random display gives brand-new segments views so they can accrue votes. | Cold segments still get a chance. |

Privacy: queries use the **k-anonymity hash-prefix** (`GET /api/segments/:sha256HashPrefix`, first ~4 chars of the title/release hash) so a node never learns which movie a household is filtering.

## Curators & trust circles (R09)

Subsidiarity without ActivityPub: a curator is just a **namespace inside the one open dataset** that households subscribe to, not a separate server.

- A household follows one or more curators whose standards match theirs (e.g. a stricter faith-based set vs. a lighter language-only set). Conflicting community norms coexist as competing/overlapping segment sets.
- **Precedence rule (the tie-breaker):** `subscribed-curator-locked > community-voted > unmoderated`. Show provenance per applied edit ("skipped by <curator>") so "why was this skipped?" is always answerable.
- Bootstrapping: start with a small maintainer-blessed curator set + open self-declared curators users subscribe to at their own risk. Curator lists are themselves forkable so no single moderator is a chokepoint. Signed, curator-scoped Git bundles are the designed upgrade path (federation R7) — not built for v1.

## The automation gate (R10)

Auto-detection solves cold-start for the biggest category (profanity/Language) cheaply — but a family-safety tool cannot ship unreviewed. Every automated path is **suggestion-only**:

```
subtitle word-list match ─┐
                          ├─→ status = 'auto_suggested', votes = 0
AI classification ────────┘              │
                                         ▼
                      human confirm (1 reviewer) OR N upvotes
                                         │
                                         ▼
                                status = 'published'  ← only these count as trusted
```

- Run subtitle↔audio alignment (ffsubsync-style) **before** deriving mute timings; subtitles drift vs. audio.
- Default auto-mutes to **whole-cue** (safe, over-mutes) rather than word-level narrowing (error-prone; the Scunthorpe problem). Reviewers tighten to word-level on confirmation.
- `auto_suggested` segments are never emitted to players and never included in public dumps as trusted — they live in the review queue only.

## Related decisions

R01 (metadata-only), R04 (fingerprint keying), R05 (taxonomy), R08 (moderation), R09 (curators), R10 (automation gate). Open items that touch this flow: Sybil/rate-limit defense depth, whether mirrors ever accept upstream submissions — see [`40-QUESTIONS-OPEN`](./40-QUESTIONS-OPEN.md).
