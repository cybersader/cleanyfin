---
title: Decisions (resolved)
description: The decision log — R01–R12, each with its rationale and source; provisionally locked pending new evidence.
sidebar:
  order: 6
---

> The agent-side decision log. Each entry: the decision, the rationale, and the source. When a decision locks in a session, add it here (and propagate to PROJECT_CONTEXT/FOCUS). "Resolved" means *provisionally locked pending new evidence* — most of these are research-backed leans, not battle-tested facts. The two things gating real code (enforcement spike, data-license) are deliberately still in [Open Questions](/cleanyfin/project/open-questions/).

Locked 2026-07-21 (initialization research fan-out; see `knowledge-base/01-working/`):

**R01 — Metadata only, never media (the legal keystone).**
cleanyfin distributes only timestamps + category metadata + edit-decisions (EDL / Media Segments), applied to media the user already owns, in the user's own player. Never host, cache, transcode, proxy, export, or decrypt A/V; never bundle clips/screenshots. *Why:* this is the exact distinction the 9th Circuit drew — ClearPlay (edit-decisions only, legal under the Family Movie Act §110(11)) vs VidAngel (made copies + circumvented DRM, lost ~$62M). SponsorBlock has run this posture for years. *Source:* [legal-and-ip-landscape](/cleanyfin/research/legal/), [prior-art-and-oss-competitors](/cleanyfin/research/prior-art-oss/) F11.

**R02 — Architecture = thin Jellyfin `IMediaSegmentProvider` plugin + small API server + companion PWA.**
Build on Jellyfin's native Media Segments (10.10+); the plugin fetches community segments from the server and emits them so native clients render skip buttons. Don't fork clients. *Why:* proven pattern (Intro Skipper, TheIntroDB, chapter-segments provider); inherits client support for free; metadata-only keeps it DMCA-safe. *Source:* [jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/) R1.

**R03 — Federation v1 = SponsorBlock model (open hub + public dumps + trivial mirrors), not S2S protocols.**
One small self-hostable hub; publish the entire dataset as periodic public dumps; make read-only mirrors a first-class documented feature (sb-mirror pattern). *Why:* delivers real anti-lock-in "federation" + offline/subsidiarity today without ActivityPub/nostr/matrix/CRDT cost. *Source:* [federation-architecture](/cleanyfin/research/federation/) R1–R2, R6.

**R04 — Version matching = fingerprint-keyed segments + per-file offset, fail-safe on low confidence.**
Key every segment set to `(title_id + release fingerprint)` where fingerprint = OpenSubtitles moviehash + exact duration; each local file resolves to a release + a user-adjustable `calibration_offset`. When confidence is low, prefer over-filtering / prompt rather than silently mis-timing. Chromaprint audio-anchor auto-align is opt-in v2. *Why:* wrong-rip timestamps are the #1 correctness risk and a trust-breaker for a family-safety tool. *Source:* [federation-architecture](/cleanyfin/research/federation/) F5/R3, [tagging-taxonomy-and-data-model](/cleanyfin/research/taxonomy/) F6/R2–R3.

**R05 — Taxonomy = fixed 9 categories × severity 0–3 + a small action enum.**
Categories (closed set for v1, free-form `tags` for the long tail): profanity, sexual_dialogue, sex_scene, nudity, violence, gore, disturbing, substance_use, crude. Severity 0–3 (none/mild/strong/extreme). Actions: mute, skip, mark (blur/crop schema-reserved, rendered as skip in v1). *Why:* all four studied rating systems converge on ~4–5 core categories; ordinal severity gives VidAngel-like control with ~9 sliders instead of 80 toggles (super-easy). Closed enum keeps federated data consistent. *Source:* [tagging-taxonomy-and-data-model](/cleanyfin/research/taxonomy/) R1.

**R06 — Default category→action map; profile resolves the actual action.**
Defaults: profanity/sexual_dialogue/crude → mute (keep video/plot); sex_scene/nudity/violence/gore/disturbing → skip; substance_use → mark. Store the default on the segment but resolve the real action at playback from the viewer's profile. *Why:* matches ClearPlay/VidAngel behavior and Jellyfin's own "clients decide the action" design, so one shared segment serves households with different preferences. *Source:* [tagging-taxonomy-and-data-model](/cleanyfin/research/taxonomy/) R4, [jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/) F1.

**R07 — MVP filtering behavior = SKIP-only (Web + Android TV); EDL export for real mute (Kodi/mpv).**
Native Jellyfin clients have no mute action yet (only skip-style). Ship skip now on Web + Android TV; export EDL (action 1 = mute) for Kodi/mpv users who want true profanity mute. Be explicit that native mute is upstream-gated. *Why:* skip is the only universally-working action today; overpromising the VidAngel word-mute would break trust. *Source:* [jellyfin-integration-mechanics](/cleanyfin/research/jellyfin/) F5/R2–R3.

**R08 — Account-free pseudonymous identity + moderation queue.**
Locally-generated UUID hashed into a public submitter ID; k-anonymity hash-prefix queries; auto-hide at vote score ≤ −2; shadowban vandals; curator-locked segments win over unlocked. No forced accounts. *Why:* matches the maintainer's cross-project dislike of account walls while giving real abuse resistance (SponsorBlock's proven recipe). *Source:* [federation-architecture](/cleanyfin/research/federation/) R4, [prior-art-and-oss-competitors](/cleanyfin/research/prior-art-oss/) F6.

**R09 — Subsidiarity via subscribable curator profiles inside one open dataset.**
A household follows curators whose standards it shares; conflicting community norms coexist as competing/overlapping segment sets with a clear precedence rule (subscribed-curator-locked > community-voted > unmoderated), not as separate servers. *Why:* honors "different communities filter differently" without ActivityPub — a small schema change (curator/namespace + subscription list). *Source:* [federation-architecture](/cleanyfin/research/federation/) R5.

**R10 — Automation is suggestion-only, human-in-the-loop.**
Subtitle+word-list profanity detection (cleanvid/monkeyplug-style) and any AI classification write `status='auto_suggested'`, votes=0; they need human confirmation (or N upvotes) to reach `published`. Run subtitle-audio alignment first. *Why:* auto-seeding solves cold-start for the biggest category (Language) cheaply, but a family-safety tool can't ship unreviewed false negatives/positives. *Source:* [tagging-taxonomy-and-data-model](/cleanyfin/research/taxonomy/) R7.

**R11 — Interop formats are first-class: MCF (.mcf/WebVTT) + Kodi EDL, import & export.**
Support both so cleanyfin interoperates with the existing MCF ecosystem, Kodi, PlexAutoSkip, Stremio CleanStream, and can seed a non-empty DB from open sources. *Why:* solves the crowdsourced cold-start problem and gives instant compatibility. *Caveat:* seed-data licensing (CC BY-NC-SA) interacts with our own data-license choice — see Q40. *Source:* [prior-art-and-oss-competitors](/cleanyfin/research/prior-art-oss/) R3.

**R12 — Convention: mirror the sibling-project scaffold.**
`.claude/` numbered stubs + `knowledge-base/` temperature gradient + a planned Astro-Starlight `docs/` site + portagenty sessions (shell/agent/docs/share-docs/tests) with Tailscale path-mount `/cleanyfin` + stupid-easy Playwright smoke testing. *Why:* consistency across Cybersader projects; fast agent onboarding. *Source:* maintainer instruction, `KNOWLEDGE_BASE_PHILOSOPHY.md`.
