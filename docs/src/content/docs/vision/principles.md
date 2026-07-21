---
title: Design Principles
description: The rules every cleanyfin design decision is checked against, derived from the project's hard constraints.
sidebar:
  order: 3
---

Use these as a checklist. If a proposed feature fails one, it is the wrong direction — reconcile against the project context (which wins) before proceeding.

1. **Metadata only, never media.** Ship only timestamps + category enums + edit-decisions (EDL / Media Segments), applied to media the user already owns in the user's own player. Never host, cache, transcode, proxy, decrypt, or export a frame of A/V — not even "for reference" thumbnails or clips. *Why:* the exact line between ClearPlay (survived 20+ years) and VidAngel (~$62M verdict); the single legal keystone. (R01; [legal research](/cleanyfin/research/legal/))

2. **Super-easy setup is a feature, not an afterthought.** The headline install is one `docker compose up` (or one static binary + systemd); backup is copying a file; a non-expert is running in ~5 minutes and it doesn't fall over. *Why:* the maintainer's north star — "super easy, and I mean super easy" — and adoption depends on it. (Hard Constraint #2; [tech-stack research](/cleanyfin/research/tech-stack/))

3. **Simplify first; defer distributed-systems machinery.** Prefer boring, proven, low-maintenance tech. Mirrors + public dumps *are* the v1 federation — no ActivityPub / nostr / matrix / shared-DB CRDTs until there's a problem they alone solve. *Why:* keeps this a maintainable side project, not a distributed-systems research project. (R03; [federation research](/cleanyfin/research/federation/) R6)

4. **No forced accounts.** Contribution safety comes from pseudonymous locally-generated submitter IDs + a moderation queue + voting + curator locks — never an account or email wall. *Why:* matches the cross-project value and still gives real abuse resistance (SponsorBlock's proven recipe). (R08)

5. **No hyperscalers, no k8s.** Self-hosting is the target; resilience is single-node — `restart: unless-stopped` + `/healthz` healthcheck + file-copy or Litestream backup. No AWS/GCP/Kubernetes. *Why:* the mission is decentralized self-host, and orchestrators break the 5-minute promise. (Hard constraint #5; [tech-stack research](/cleanyfin/research/tech-stack/) R5)

6. **Build on upstream Jellyfin, don't fork it.** Register as a standard Media Segments provider and ride the native skip UI; track upstream mute-action progress rather than hacking the playback pipeline or forking clients. *Why:* inherits client support for free and keeps the plugin trivial across ABI bumps. (R02; [Jellyfin integration research](/cleanyfin/research/jellyfin/))

7. **Fail safe on low match-confidence.** Every segment set keys to `(title_id + release fingerprint = moviehash + exact duration)`; when the local file doesn't match, surface "no verified data for this exact file" rather than apply the wrong rip's timestamps. Prefer over-filtering or prompting over silent mis-timing. *Why:* wrong timestamps are the #1 correctness risk and a trust-breaker for a family-safety tool. (R04; [federation research](/cleanyfin/research/federation/) R3)

8. **Automation suggests; humans confirm.** Subtitle/word-list profanity detection and any AI classification write `status='auto_suggested'` only; a human (or N upvotes) must promote them to `published`. *Why:* auto-seeding cheaply solves cold-start, but a family-safety tool can't ship unreviewed false negatives/positives. (R10)

9. **Subsidiarity via curators, one open dataset.** Different communities filter differently — modeled as subscribable curator profiles inside one open dataset (precedence: subscribed-curator-locked > community-voted > unmoderated), not as separate servers or one global truth. *Why:* honors "communities filter differently" with a small schema change instead of a fediverse. (R09; [federation research](/cleanyfin/research/federation/) R5)

10. **Open data, anti-lock-in.** The whole dataset publishes as periodic public dumps; read-only mirrors are a documented first-class feature; the dump schema is designed now to become signed, forkable Git bundles later. Pick the data license deliberately and up front (it's effectively irreversible for a crowdsourced DB). *Why:* the server + its open dataset are the product; dumps and mirrors are what make "federated" real. (R03, R11; [legal research](/cleanyfin/research/legal/) R4)

---

**The two-line test.** Before any feature: *(a) Does it move a single frame of A/V, decrypt anything, or create a fixed edited copy?* If yes, stop (violates #1). *(b) Does it make the 5-minute self-host harder or add an account wall / orchestrator / heavy protocol?* If yes, justify it hard against #2–#5.

See also: [Vision](/cleanyfin/vision/vision/), [Tradeoffs](/cleanyfin/project/tradeoffs/), and the open calls in [Open Questions](/cleanyfin/project/open-questions/).
