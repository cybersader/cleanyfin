# Trade-offs — the honest tensions

> 📎 Pointer stub. Backed by all six deep-dives in [`../knowledge-base/01-working/`](../knowledge-base/01-working/) (esp. `jellyfin-integration-mechanics.md`, `federation-architecture.md`, `tagging-taxonomy-and-data-model.md`, `tech-stack-and-devops.md`). Siblings: [`21-ARCHITECTURE`](./21-ARCHITECTURE.md), [`22-DATA-MODEL`](./22-DATA-MODEL.md), [`40-QUESTIONS-OPEN`](./40-QUESTIONS-OPEN.md).

Every v1 call below is a deliberate simplification (hard constraint #3: simplify first). None are free — each carries a residual risk we choose to accept, and say so out loud. Where a call is still genuinely undecided, it lives in [`40-QUESTIONS-OPEN`](./40-QUESTIONS-OPEN.md) instead.

## The tensions

### 1. Skip vs. mute — the VidAngel word-mute isn't fully available yet
- **Tension:** The signature "mute the profanity, keep the plot" experience needs a client MUTE action. Native Jellyfin clients (Web, Android TV) today only *skip*; a real mute exists only via EDL (action 1) on Kodi/mpv, and native mute is upstream-gated (Jellyfin feature #3396, "in the works").
- **v1 call (R07):** SKIP-only on native clients; **EDL export** delivers true mute for Kodi/mpv. Be explicit about the gap; add native mute when upstream ships it.
- **Residual risk:** Skipping muted dialogue removes plot, so we do *not* silently fall back mute→skip on native clients — profanity-heavy titles have a degraded experience there until upstream lands. Over-promising word-mute would break trust worse.

### 2. Granularity vs. usability — 9 sliders vs. VidAngel's ~80 toggles
- **Tension:** VidAngel's ~80 per-title filters give surgical control but overwhelm setup. Too few axes and power users can't express what they want.
- **v1 call (R05):** Fixed **9 categories × severity 0–3** + free-form `tags` for the long tail. Granularity lives in the DATA (every segment individually tagged) but is COLLAPSED to ~9 sliders in the default UX; per-segment overrides sit behind an "advanced" path.
- **Residual risk:** Some objections (religious-values, LGBT themes, specific phobias, blasphemy-as-flag-vs-severity) don't fit 9 categories cleanly; `tags` only partly mitigates. A closed enum is what keeps federated data consistent — that's the trade we're buying.

### 3. Federation cost — mirrors + dumps now vs. true S2S later
- **Tension:** "Federated" purists expect ActivityPub/nostr/matrix. Those import heavy moderation/ops cost (defederation fragmentation, event expiry) for a small append-mostly timestamp DB.
- **v1 call (R03):** One open hub + full periodic public dumps + trivial read-only mirrors (sb-mirror pattern). Design the **signed Git-bundle** upgrade path now; build it later.
- **Residual risk:** WRITES funnel to one hub — a single point of failure/abuse/legal target for submissions. Mitigated because reads survive via mirrors and any fork can become the new hub with zero data loss. Decentralization purists may be disappointed until the Git-bundle phase.

### 4. Version matching — moviehash + duration "good enough" vs. progressive drift
- **Tension:** A timestamp is worthless against the wrong rip. moviehash (filesize + first/last 64KB) + exact duration nails the *exact file* cheaply, but a single per-file offset can't fix **progressive drift** (23.976 vs 25fps PAL speedup) or auto-generalize across rips.
- **v1 call (R04):** Fingerprint-keyed segments + one user-adjustable `calibration_offset_ms`; **fail safe** (prefer over-filter / prompt) on low confidence. Chromaprint audio-anchor auto-align is opt-in v2.
- **Residual risk:** Coverage is per-exact-file and can be sparse (moviehash breaks on re-mux, collides on same-size files); PAL-drift and wrong-cut auto-matches need the deferred audio-fingerprint / ffsubsync layers. A silently mis-timed filter is the worst failure mode, hence fail-safe over guess.

### 5. SQLite (single-writer) vs. Postgres scale
- **Tension:** SQLite-WAL is a copy-a-file backup and zero external services (hard constraint #2, "super-easy setup"), but it's effectively single-writer.
- **v1 call (R05 stack):** SQLite-WAL default (`modernc.org/sqlite`, optional Litestream); Postgres only at SponsorBlock scale.
- **Residual risk:** A very high submission/vote write rate would eventually bottleneck. Accepted: a self-hosted household/community node never approaches that; the schema is portable to Postgres if a public mega-hub ever needs it.

### 6. Per-profile enforcement lives outside Jellyfin's security model
- **Tension:** Media Segments are **global per library item**, not per-user. There is no server-side hook guaranteeing a kid's client honors the filter — it's client-cooperative, so technically bypassable.
- **v1 call:** Accept **client-side opt-in** for MVP and be honest about the trust boundary; map profiles onto Jellyfin users 1:1 to reuse existing auth/parental controls. A true per-user enforcement layer is a fast-follow (Spike A gates it).
- **Residual risk:** A determined user on an uncooperative client can bypass filtering. For the target use (families who *want* filtering, not adversarial DRM), acceptable for v1 — but it must not be sold as tamper-proof. See Q3 in [`40-QUESTIONS-OPEN`](./40-QUESTIONS-OPEN.md).

### 7. Blur/crop deferred
- **Tension:** Blur/crop would soften nudity/gore without a full skip, but needs real-time video processing / re-encode — far harder than a seek (skip) or `volume=0` (mute).
- **v1 call (R05/R06):** Ship mute + skip + mark. Keep blur/crop as **schema-reserved** valid actions (data stays future-proof) but **render them as skip** with a visible notice.
- **Residual risk:** Users expecting a blurred scene get a hard skip instead; the visible notice is the only mitigation until a processing pipeline exists.

### 8. Bypass workflow kept minimal
- **Tension:** The VidAngel "request an exception" escape hatch ideally pushes an approve/deny to a parent's phone.
- **v1 call:** Admin toggles a **time-boxed, per-title exception** in the dashboard (`REQUESTED → APPROVED{expires_at} | DENIED → EXPIRED`). Push-notification approval and per-profile request queues deferred.
- **Residual risk:** No async parent-approval UX in v1; a blocked title needs an admin at the dashboard. Accepted to preserve "super easy."
