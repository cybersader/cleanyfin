# Cleanyfin — Source-bound plan, measured adapter

**ADOPTED · 2026-11-21 · owner-approved.** A household should be able to change players without re-authoring its marks. It should never have to guess whether “filtered” means the player will actually omit anything. Keep one versioned description of **what must happen**, translate it at the last responsible point into **what this player understands**, and offer playback only within the combination actually tested.

```text
accepted annotations + verified edition/asset binding + household policy
    -> private playback plan (source clock, required actions, revisions, expiry)
    -> eligible delivery adapter -> existing player -> measured presentation
                       ^
            client / version / mode / action evidence
```

## Rules and reasons

| Rule | Why it matters here |
|---|---|
| Bind operations to the selected original and its presentation clock. Keep every interval outside the requested change. | A title, OSHash or matching runtime does not establish timing. A mux offset moved a browser boundary by 21.406 ms. Cutting an entire keyframe group to be “safe” discards wanted material. |
| Keep the durable contract independent of any player, transport, or policy author. | A community tag, a parent's decision and an mpv instruction answer different questions. Kodi consumes **removed** ranges; mpv concatenates **kept** ranges. Neither file is the plan. |
| Reuse existing protocols at the edge. Keep household decisions local. | Wholphin opened an mpv descriptor without a custom client; the shared database need not know who watched. One household plugin should suffice, without a transcoder, new gateway service or writable movie mount. |
| Separate presentation from access control. | mpv fetched even the marked original bytes. A cooperative player can omit them without creating an edited movie; this does not prevent deliberate access to the original or prove the annotations cover the entire film. |
| Offer only what the evidence supports. | Zero streams made Wholphin choose transcode; original stream metadata let it direct-play. Neither a DirectPlay flag nor zero marked frames in a sampled silent recording proves sound or kept frames at a splice. |

## Invariants

- The plan fixes the exact source, viewer policy, accepted annotation revisions, clock, half-open source-time intervals and required actions. It contains **no media, paths, playback credentials or client commands**. Replacing the source, policy or accepted input invalidates delivery; a plan is not an access token.
- An adapter preserves all required semantics or refuses the whole plan. Never silently turn mute into cut, drop an unknown action, extend a cut, or play the original. Unsupported, unavailable, expired, changed or unmeasured means **no filtered playback offer**; an original is a separately identified, separately authorized choice.
- A delivery claim names an adapter revision, player/build, backend, device/output mode, server tuple, source/track bounds, actions, controls, measured leakage and kept loss, and the limits of observation. For cut admission, [R21](41-QUESTIONS-RESOLVED.md) requires a complete cut-position sweep with zero marked picture or sound, exact kept picture and PCM, and only one possible extra kept sample before a fresh start or resume near a join. Disclose that sole exception in the reviewed row; a failure or an unmeasured position refuses. Hold a partial result as experimental; withdraw approval when the tuple changes. No universal “supports EDL” flag.
- Cleanyfin distributes decisions, not audio/video. Jellyfin serves the authorized original as usual. No stored edited media, including “temporary” replayable packages; no shared household profiles, tickets or viewing history.

## How to extend it

| Addition | What changes | What does not |
|---|---|---|
| Adapter (mpv EDL first; perhaps Kodi, HLS, Media3 clips or a browser schedule) | A translator, an explicit domain it accepts, rejection cases, timeline mapping and end-to-end evidence | Source identity and household policy. An HLS adapter that *creates media* also needs a separate scope/legal decision. |
| Client or backend | A distinguishable capability row and measured controls on that tuple; a new player need not mean a new translator | Plan version and existing approvals |
| Action (mute, later perhaps cover) | Specify required picture, sound and clock behavior, interactions and test oracle; use a required feature/new version if old readers could misinterpret it | Old readers refuse unfamiliar requirements. Cover is not cut; no implicit substitution. |
| Plan input (local marks, curator snapshot, licensed interchange) | Map provenance, units, rights, review status and exact binding into the **same** household resolver | Import never grants authority, embeds executable instructions or bypasses review. |

**Outside this pattern:** owning a client; DRM or bypass resistance; completeness ratings; automatic alignment of different edits; approval of any server-cut stream. It is a delivery rule, not a new federation protocol or legal opinion.

**Before adding anything to playback:** (1) Name the household decision and the exact source clock. (2) Identify the existing protocol and which semantics it cannot carry. (3) Show where authentication, profile and revision are checked, including retries and failure. (4) Show that neither media storage nor an unfiltered fallback is introduced. (5) Run a positive control, score marked **and kept** picture and sound through a join, seek and resume, and publish the exact limitations. (6) Register or refuse that tuple; removal of the adapter must leave the original and annotations intact. (7) Add or update the row in the [upkeep ledger](32-UPKEEP-LEDGER.md) for anything new this depends on.

[First plugin slice and initial evidence register](../knowledge-base/01-working/delivery-design-2026-11-21/01-plan-and-mpv-adapter-design.md) · [Wholphin measurements](../knowledge-base/01-working/client-ecosystem-2026-11-21/google-tv-clients.md#follow-up-test-2026-11-21-same-item-but-with-the-originals-stream-metadata) · [Cross-path measurements](../knowledge-base/01-working/playback-paths-2026-11-21/README.md) · [Browser clock evidence](../knowledge-base/01-working/long-term-architecture-2026-10-07/10-client-boundary-and-browser-proof.md)
