# Resolved Questions — Decision Log

> The agent-side decision log. Each entry: the decision, the rationale, and the source. When a decision locks in a session, add it here (and propagate to `PROJECT_CONTEXT`/`FOCUS`). "Resolved" means *provisionally locked pending new evidence* — most of these are research-backed leans, not battle-tested facts. The two things gating real code (enforcement spike, data-license) are deliberately still in `40-QUESTIONS-OPEN`.

## Dated factual correction and supersession — 2026-10-08

Keep R01–R15 below as historical records; numbering and recorded R15 license selection are unchanged. No architecture choice was approved by workflow approval. The [reviewed proposal](../knowledge-base/01-working/long-term-architecture-2026-10-07/04-architecture-proposal.md) and [independent review](../knowledge-base/01-working/long-term-architecture-2026-10-07/06-independent-review.md) are proposed analysis, not a new decision log.

- **R01/R02:** metadata-only distribution is not legal, patent, trademark or import clearance. Native provider integration does not inherit filtering support: current output is Unknown with action/category discarded, and Web v10.11.11 defaults Unknown to None.
- **R03/R04:** dump exists; importer, replication, private overlays and calibration do not. Lookup is fingerprint-only, not title/hash/duration verification. Duration/hash cannot prove equivalent cuts.
- **R05/R06:** taxonomy is stored; private profile resolution and authorized unsupported-action substitution are proposed, not native DTO capabilities.
- **R07/R11:** historical fleet findings do not prove Cleanyfin behavior. Pinned Web has skip/prompt guards; session Seek/Mute/Unmute exists without an automatic scheduler. Kodi/mpv EDL formats are not equivalent; adapters need separate tests.
- **R08/R09/R10:** pending rows are public above the vote threshold; claimed identities are unauthenticated, prefix queries guarantee no anonymity set, and quarantine/curators/private policy are not implemented. Votes cannot establish parental authority or consent. Revision-bound curation and legacy publication remain owner-controlled.
- **R13:** proxy-as-enforcement is REJECTED. Per-user metadata selection does not force actions or prevent original-media bypasses. Provider generation is user-blind, but authenticated item lookup can be user-aware. The owner has settled sequencing: cooperative supported-client filtering first, server-side enforcement/bypass resistance retained longer term. Its threat model and implementation remain open.
- **R14:** current controller forwards to the Go API; no immediate native insertion is established. Ordinary refresh can clear provider materialization under the reviewed execution/cancellation preconditions; force-overwrite pre-deletes rows. No live reproduction or preservation fix is claimed.
- **R12/R15:** historical scaffolding/builds and recorded license choice remain history, not prospective build validation, contributor-rights evidence or clearance.

Evidence baseline: `2426cef1ab345a5a829b2ce7a6fe8fab8661011e`; pinned Jellyfin/Web v10.11.11, sources accessed 2026-10-08. Runtime compatibility, authorization exploitability, recovery, capacity and legal clearance remain UNVERIFIED.

## Owner decision — product sequencing, 2026-10-08

**Decision:** dependable filtering on explicitly supported cooperative clients is the owner-approved first-release target. Server-side enforcement/deliberate-bypass resistance is a retained LONGER-TERM product objective, not abandoned and not a first-release prerequisite. *Source:* owner response, “Yep but keep server-side enforcement on the longer-term roadmap”.

This settles sequencing only. Actual playback correctness remains UNVERIFIED and gated on experiments. The broader architecture, enforcement technique, first client/actions, failure policy, exposure tolerance, timing/SLO targets, public launch, trust/privacy, recovery/maintenance budgets and legal clearance are not approved by this decision.

The retained [roadmap track](./20-ROADMAP.md) requires a defined threat model, feasibility spike, original-media/alternate-client/download/file-share access and authorization analysis, client/format compatibility and operating cost, reliable failure/recovery behavior, applicable legal review and evidence before claiming enforcement. Metadata filtering or remote commands alone are not unbypassable playback enforcement. No claim of enforceability against a server administrator or someone controlling the media is made; later design must define scope. R01–R15 and Q identifiers remain unchanged.

## Historical entries — superseded only where corrected above

Locked 2026-07-21 (initialization research fan-out; see `knowledge-base/01-working/`):

**R01 — Metadata only, never media (the legal keystone).**
cleanyfin distributes only timestamps + category metadata + edit-decisions (EDL / Media Segments), applied to media the user already owns, in the user's own player. Never host, cache, transcode, proxy, export, or decrypt A/V; never bundle clips/screenshots. *Why:* this is the exact distinction the 9th Circuit drew — ClearPlay (edit-decisions only, legal under the Family Movie Act §110(11)) vs VidAngel (made copies + circumvented DRM, lost ~$62M). SponsorBlock has run this posture for years. *Source:* `legal-and-ip-landscape.md`, `prior-art-*` F11.

**R02 — Architecture = thin Jellyfin `IMediaSegmentProvider` plugin + small API server + companion PWA.**
Build on Jellyfin's native Media Segments (10.10+); the plugin fetches community segments from the server and emits them so native clients render skip buttons. Don't fork clients. *Why:* proven pattern (Intro Skipper, TheIntroDB, chapter-segments provider); inherits client support for free; metadata-only keeps it DMCA-safe. *Source:* `jellyfin-integration-mechanics.md` R1.

**R03 — Federation v1 = SponsorBlock model (open hub + public dumps + trivial mirrors), not S2S protocols.**
One small self-hostable hub; publish the entire dataset as periodic public dumps; make read-only mirrors a first-class documented feature (sb-mirror pattern). *Why:* delivers real anti-lock-in "federation" + offline/subsidiarity today without ActivityPub/nostr/matrix/CRDT cost. *Source:* `federation-architecture.md` R1–R2, R6.

**R04 — Version matching = fingerprint-keyed segments + per-file offset, fail-safe on low confidence.**
Key every segment set to `(title_id + release fingerprint)` where fingerprint = OpenSubtitles moviehash + exact duration; each local file resolves to a release + a user-adjustable `calibration_offset`. When confidence is low, prefer over-filtering / prompt rather than silently mis-timing. Chromaprint audio-anchor auto-align is opt-in v2. *Why:* wrong-rip timestamps are the #1 correctness risk and a trust-breaker for a family-safety tool. *Source:* `federation-*` F5/R3, `tagging-taxonomy-*` F6/R2–R3.

**R05 — Taxonomy = fixed 9 categories × severity 0–3 + a small action enum.**
Categories (closed set for v1, free-form `tags` for the long tail): profanity, sexual_dialogue, sex_scene, nudity, violence, gore, disturbing, substance_use, crude. Severity 0–3 (none/mild/strong/extreme). Actions: mute, skip, mark (blur/crop schema-reserved, rendered as skip in v1). *Why:* all four studied rating systems converge on ~4–5 core categories; ordinal severity gives VidAngel-like control with ~9 sliders instead of 80 toggles (super-easy). Closed enum keeps federated data consistent. *Source:* `tagging-taxonomy-and-data-model.md` R1.

**R06 — Default category→action map; profile resolves the actual action.**
Defaults: profanity/sexual_dialogue/crude → mute (keep video/plot); sex_scene/nudity/violence/gore/disturbing → skip; substance_use → mark. Store the default on the segment but resolve the real action at playback from the viewer's profile. *Why:* matches ClearPlay/VidAngel behavior and Jellyfin's own "clients decide the action" design, so one shared segment serves households with different preferences. *Source:* `tagging-taxonomy-*` R4, `jellyfin-*` F1.

**R07 — MVP filtering behavior = SKIP-only, on a wider fleet than first assumed; EDL for real mute (Kodi/mpv).**
Native Jellyfin clients still have **no** mute action (verified 2026-07-21, Spike C — 18 months after it was called "in the works"). Ship skip now. **Updated fleet (Spike C):** native Media Segments skip ships on **Web + Android TV + Roku (3.0.0) + Kodi (native, via `jellyfin-kodi`)**, with **webOS partial** (auto-skip works, ask-to-skip button broken) and **Swiftfin/iOS the known gap** (open request #1525). Real per-word **mute stays EDL-only** — and because `endrl/jellyfin-plugin-edl` is stale, cleanyfin should **emit EDL from its own data** rather than depend on it. Be explicit that native mute is upstream-gated (track #3396 / jellyfin-meta #30). *Why:* skip is the only action working across clients; single-word profanity collapses to a coarse skip, which will feel worse than VidAngel until upstream mute lands (a product-honesty point). *Source:* `spike-c-client-support.md`, `jellyfin-integration-mechanics.md`.

**R08 — Account-free pseudonymous identity + moderation queue.**
Locally-generated UUID hashed into a public submitter ID; k-anonymity hash-prefix queries; auto-hide at vote score ≤ −2; shadowban vandals; curator-locked segments win over unlocked. No forced accounts. *Why:* matches the maintainer's cross-project dislike of account walls while giving real abuse resistance (SponsorBlock's proven recipe). *Source:* `federation-architecture.md` R4, `prior-art-*` F6.

**R09 — Subsidiarity via subscribable curator profiles inside one open dataset.**
A household follows curators whose standards it shares; conflicting community norms coexist as competing/overlapping segment sets with a clear precedence rule (subscribed-curator-locked > community-voted > unmoderated), not as separate servers. *Why:* honors "different communities filter differently" without ActivityPub — a small schema change (curator/namespace + subscription list). *Source:* `federation-architecture.md` R5.

**R10 — Automation is suggestion-only, human-in-the-loop.**
Subtitle+word-list profanity detection (cleanvid/monkeyplug-style) and any AI classification write `status='auto_suggested'`, votes=0; they need human confirmation (or N upvotes) to reach `published`. Run subtitle-audio alignment first. *Why:* auto-seeding solves cold-start for the biggest category (Language) cheaply, but a family-safety tool can't ship unreviewed false negatives/positives. *Source:* `tagging-taxonomy-and-data-model.md` R7.

**R11 — Interop formats are first-class: MCF (.mcf/WebVTT) + Kodi EDL, import & export.**
Support both so cleanyfin interoperates with the existing MCF ecosystem, Kodi, PlexAutoSkip, Stremio CleanStream, and can seed a non-empty DB from open sources. *Why:* solves the crowdsourced cold-start problem and gives instant compatibility. *Caveat:* seed-data licensing (CC BY-NC-SA) interacts with our own data-license choice — see Q40. *Source:* `prior-art-*` R3.

**R12 — Convention: mirror the sibling-project scaffold.**
`.claude/` numbered stubs + `knowledge-base/` temperature gradient + an Astro-Starlight `docs/` site (**stood up 2026-07-21**, builds + smoke-tests green) + portagenty sessions (shell/agent/docs/share-docs/tests) with Tailscale path-mount `/cleanyfin` + stupid-easy Playwright smoke testing. *Why:* consistency across Cybersader projects; fast agent onboarding. *Source:* maintainer instruction, `KNOWLEDGE_BASE_PHILOSOPHY.md`.

---

Locked 2026-07-21 (feasibility spikes A/B/C — verified against Jellyfin 10.11 source; see `spike-a-enforcement.md`, `spike-b-segment-write-api.md`, `spike-c-client-support.md`):

**R13 — Enforcement architecture: default global provider (honest opt-in) + optional cleanyfin response-filtering proxy for real per-profile enforcement.** (resolves Q3 / Spike A)
Verified from 10.11 source: **no seam in Jellyfin's segment pipeline carries per-user context** — `IMediaSegmentProvider.GetMediaSegments` receives only `ItemId` + `ExistingSegments` (user-blind by contract), and `GET /MediaSegments/{itemId}` returns the global set (only a whole-item parental/ACL check is user-aware). So per-profile enforcement is **not obtainable from the provider system**. Decision: **(1) default install** ships a standard `IMediaSegmentProvider` emitting a global segment set; per-profile category *selection* is client-side/opt-in, with the trust boundary documented honestly. **(2) Optional "enforced households" mode** = a thin, opt-in **cleanyfin reverse-proxy/companion** that reads the Jellyfin auth token on each `GET /MediaSegments/{itemId}`, maps user→cleanyfin profile, and **filters the response JSON** — true per-profile enforcement for *unmodified native clients*, staying strictly metadata-only, coupled only to the **stable public HTTP contract** (one extra container, not k8s). **(3) Explicitly do NOT** build enforcement on `ISessionManager` playback events (fragile — broke in 10.11) or per-user physical-file scoping (breaks super-easy setup); use parental/tag gates only for whole-title blocking. *Why:* this is "do it ourselves," done safely on the one upstream surface that is both user-attributable and version-stable — exactly the maintainer's "if tied to upstream, make sure it's trustworthy, else do better ourselves." *Source:* `spike-a-enforcement.md`.

**R14 — Segment write path: PWA → cleanyfin's Go API (source of truth); plugin materializes segments + hosts its OWN thin write controller.** (resolves Q4 / Spike B)
Verified: **core Jellyfin has no segment create/delete endpoint** (10.10 or 10.11) — only `GET`. The community write route (`/MediaSegmentsApi`) was folded into Intro Skipper and rewritten to be coupled to *its* analysis DB + 5 fixed modes. Decision: the marking PWA POSTs in/out + rich category to **cleanyfin's Go crowdsource API** (source of truth, pseudonymous, no admin token in the client); cleanyfin's `IMediaSegmentProvider` materializes those into Jellyfin at scan/refresh; the plugin hosts **its own ~90-line write controller** under cleanyfin's route namespace (copying the *original* `jellyfin-plugin-ms-api` pattern over `IMediaSegmentManager`) only for *live* insert-without-rescan. **Do not depend on Intro Skipper's route.** *Source:* `spike-b-segment-write-api.md`.

**Correction (Spike B) — the shipped Jellyfin `MediaSegmentDto` has only `Id, ItemId, Type, StartTicks, EndTicks`.** No `StreamIndex`, no `Action`, no `Comment` (those were in the design *proposal* PR #10530, not the shipped model). cleanyfin's rich fields (severity, action, category, votes, provenance) therefore live **entirely in cleanyfin's own DB**; Jellyfin stores only the coarse 6-type enum + tick span. Any earlier grounding that listed `Action`/`StreamIndex` on the Jellyfin segment is superseded here.

---

Locked 2026-07-21 (maintainer decision):

**R15 — Licensing: `CC0-1.0` for the dataset + `AGPL-3.0-or-later` for the code.** (resolves Q2)
The crowdsourced content-segment **dataset** is dedicated to the public domain under **CC0-1.0**; all **source code** (Go server, Jellyfin plugin, companion PWA, docs tooling, scripts) is **AGPL-3.0-or-later**. *Why:* timestamp/category data is factual (thin-to-no copyright, *Feist*), so CC0 reflects reality and maximizes the locked values — federation, mirroring, reuse, subsidiarity — while killing the "is a paid self-host commercial?" **NC ambiguity** that constrains SponsorBlock/MCF. Copyleft give-back is placed where it's enforceable and meaningful: the **server code** via AGPL. *Accepted consequence:* cleanyfin **cannot bulk-ingest CC-BY-NC-SA data** (SponsorBlock, MovieContentFilter) into a CC0 dataset — SponsorBlock is only a *design* template anyway (wrong domain), and MCF stays a **format** interop target (`.mcf`/EDL, R11), not a data source. **Cold-start** is solved by automated subtitle/speech profanity generation (on the user's own media / public-domain films) + original crowdsourced contributions. *Before seeding any third-party set,* verify its license (e.g. VideoSkip). Files: `LICENSE` (AGPL-3.0), `DATA-LICENSE` (CC0). *Source:* license analysis in `legal-and-ip-landscape.md` / `prior-art-and-oss-competitors.md` / `federation-architecture.md`; maintainer decision.

---

Locked 2026-11-24 (owner decisions on the [marking foundation design](../knowledge-base/01-working/marking-foundation-2026-11-24/01-marking-foundation-design.md), section 8):

**R16 — Household marking data (captures, marks, approvals, roles, audit log) lives in the household Jellyfin plugin.** Jellyfin logins supply identity and default roles; private data stays at home; approved snapshots are written where the resolver already reads them. The store interface stays shaped so a separate household service remains possible. The shared hub never holds private notes or roles. *Owner note, same day:* heavy work — AI mark suggestions, audio fingerprinting, alignment across cuts — will likely need its own household container or service, possibly on a different machine with specific hardware (a GPU or other accelerator) rather than wherever Jellyfin runs. That is expected, not a reversal: such a worker reads media and submits *suggested* captures through the same store interface, and never approves or publishes. The plugin is the primary product: a plugin-only install must stay fully usable for marking, review and filtering. The plugin remains the record of truth and the service a replaceable, optional companion. *Relation to R14:* R14's Go API remains the path for shared, public contributions; it is no longer the source of truth for a household's own marks. *Source:* owner answer, recommended option.

**R17 — One person may both mark and approve.** Every approval is recorded in the audit log; a household may turn on "second reviewer required". *Source:* owner answer, recommended option.

**R18 — A mark press does not pause the TV by default.** Captures are rough and are refined later. Pause-to-mark may become an opt-in setting only after a given app is measured to obey a remote pause. *Source:* owner answer, recommended option.

---

Locked 2026-11-25 (owner decisions on the [common formats results](../knowledge-base/01-working/common-formats-2026-11-24/01-common-formats.md)):

**R19 — A subtitle line that overlaps a mark is hidden.** Any cue whose time span touches a marked interval is suppressed for filtered playback. Until the player integration can do that and it is measured, files whose default subtitle track would show such a cue stay refused. Showing words from a removed part after the cut is not acceptable. *Source:* owner answer, recommended option.

**R20 — "Exact" stays strict for AAC.** Kept sound must match the uninterrupted original sample for sample; inaudible decoder differences (for example AAC noise substitution) are not accepted. AAC layouts stay refused while the next step measures how real AAC movies are encoded and looks for a path that is truly exact. *Source:* owner answer, recommended option.

---

Locked 2026-11-25 (owner decision on the [boundary sweep](../knowledge-base/01-working/common-formats-2026-11-24/02-boundary-sweep.md); closes open question S11):

**R21 — Admission standard: zero marked material is absolute; one extra kept sample at a fresh start is tolerated and disclosed.** A layout is admitted only if, at every position of the cut-position sweep on that server line: no marked picture and no marked sound sample is ever presented; straight cuts and seeks keep every kept frame and every kept sample exactly; and a fresh start or resume near a join deviates from the uncut original at most by one extra *kept* sample (21 µs at 48 kHz) presented before the start point. Any other kept-sound difference (altered, lost or shifted samples, including AAC noise substitution under R20) still refuses. Docs and evidence rows state the tolerance. MKV Dolby stays refused (decoder history at joins). The owner also chose to have the upstream mpv rounding fix reported, so the tolerance can be removed once mpv rounds to the nearest sample. *Source:* owner answer, recommended option, after a timeline diagram of the extra sample.

---

Locked 2026-11-25 (owner decision after watching worst-case lab clips; supersedes the kept-sound clauses of R20 and R21 for admission):

**R22 — Admission is judged by what a viewer experiences.** Asked "Should the rule for 'good enough to offer filtered' be about what a viewer experiences? ... nothing marked ever gets through; no added silence, pop, freeze or missing picture; and lips back in sync within a split second", the owner chose "Yes; Dolby now, FLAC later (Recommended)". A layout is admitted on a server line only if, at every position of the cut-position sweep (straight cut, fresh starts, seek-and-resume):

1. **Nothing marked, ever (absolute).** No marked frame and no marked sound is presented. This must be shown positively; a sample whose identity cannot be proven is not assumed to be kept.
2. **No added silence:** no run of silence at a join longer than the ideal cut's.
3. **No added pop:** the join is not audibly harsher than the ideal hard cut (measured high-frequency transient energy no higher than the ideal's, within measurement tolerance).
4. **Picture intact:** no kept frame missing, repeated, or frozen beyond what the uncut original does.
5. **Lips in sync:** sound and picture are back within ordinary playback jitter of each other within a split second (about 100 ms) of the join, and stay there.
6. **Kept-sound loss is capped and disclosed:** at most about 40 ms of kept sound may be dropped at a join (measured worst case: a mark at the very start of a movie, Dolby).

Admitted now: Dolby (AC-3/E-AC-3) layouts that meet all six on that line. FLAC stays refused until its worst case (up to 65 ms dropped mid-movie, which can clip a syllable onset) is fixed or the owner has watched the clips and accepted it. AAC stays refused (it can present marked sound or picture). Desktop-mpv lab evidence only; TV and HDMI still need their own measurement. Sample-exact kept sound remains the long-term goal (upstream mpv rounding fix), not the admission bar.

---

Locked 2026-11-25 (owner answer in the [onset decision dialog](t3-thread://v2/c9bd2e20-d8f9-45de-b0d4-30086d5bfb6d/bf5c8388-c0c3-41e4-a80a-0284ccce8171); amends only the onset part of R22):

**R23 — The first sound sample at a start or resume on a skip may differ.** Asked “This first-sample difference is the only thing keeping Dolby movies off: a tiny tick at worst, only when you press play or resume exactly on a skip. Should it count as passing?” The owner selected “Yes, allow it (Recommended) — A one-sample difference at the very first instant of starting or resuming playback counts as passing, and the docs say so. Everything else in the viewer rule stays as it is.”

The exception is only for a fresh start or in-session resume **targeting the skip itself**. Omit exactly one 48 kHz sample frame (all channels, about 21 µs) from the onset transient window before filtering, with the same omission on the ideal and matched uncut controls and the same repeat-noise calibration. That sample must still be proven kept; R21's cap of one extra kept sample before the landing relative to the uncut start still applies. Every other R22 check, the unmodified splice comparison, and the onset checks at other targets remain in force. No blanket Dolby admission: each exact desktop-mpv tuple still needs its own complete passing evidence. FLAC and AAC stay refused.

---

Locked 2026-11-25 (owner decision after the R22/R23 sweep found no Dolby layout passing on stock mpv):

**R24 — A separate, opt-in test tier for filtered playback.** Asked "Marked scenes never got through in Dolby movies, but the desktop player can't pass the strict seamless-skip test. Where should the work go next?", the owner chose "Test-only on TV, then measure (Recommended) — Let Dolby movies play filtered in test mode on the TV, still requiring that nothing marked ever gets through. Small seam issues get reported, not treated as blockers. Then measure the real TV with the HDMI adapter. Meanwhile, keep building marking."

- **Gate for the test tier (absolute):** nothing marked is ever presented at any sweep position: no marked frame, and no marked sound detected by a calibrated positive detector. Unproven per-sample identity of lossy sound does not block the test tier only when that detector clears the take; it still blocks the full tier.
- **Reported, not gating:** the R22 seam measures (silence, pop, kept picture, sync, kept-sound loss). They are recorded per layout and shown to the household.
- **Opt-in and labelled:** off by default. Only a household admin can turn it on. Filtered items offered under it are clearly marked as test playback. It never weakens the full tier (R22/R23), which remains the bar for normal filtered playback.
- **Scope:** Dolby (AC-3/E-AC-3) layouts first. AAC stays refused (marked sound was presented). FLAC stays refused pending the owner. Each player tuple (desktop mpv, Wholphin on the emulator, Wholphin on the onn. box over HDMI) needs its own test-tier measurement; desktop evidence does not carry over to the TV.

