# cleanyfin — Vision (Short)

> 📎 Pointer stub — the elevator pitch and the one-year picture. Synthesized from all six deep-dives; backed most directly by [`../knowledge-base/01-working/federation-architecture.md`](../knowledge-base/01-working/federation-architecture.md) and [`../knowledge-base/01-working/tech-stack-and-devops.md`](../knowledge-base/01-working/tech-stack-and-devops.md). Identity locked in [`PROJECT_CONTEXT.md`](./PROJECT_CONTEXT.md).

## The elevator pitch

cleanyfin is an open-source, self-hosted **content-filtering layer for Jellyfin**, backed by a **federated, crowdsourced database of tagged segments**. It gives you the VidAngel *experience* (skip/mute profanity, violence, nudity — gated per viewer profile) on a SponsorBlock *data model* (community-submitted, voted, moderated timestamps anyone can mirror).

It is **DMCA-safe by construction**: cleanyfin ships **only** timestamps + category metadata + edit-decisions, applied in real time to media the user already owns, in the user's own player. It never hosts, caches, transcodes, proxies, or decrypts a single frame of A/V — the exact line ClearPlay stayed behind and VidAngel crossed (R01, [`../knowledge-base/01-working/legal-and-ip-landscape.md`](../knowledge-base/01-working/legal-and-ip-landscape.md)).

Category word: **"layer/filter."** Not a VidAngel clone, not a media server.

## What success looks like in one year

A Jellyfin admin should be able to:

1. **Install one plugin** — a thin C# `IMediaSegmentProvider` added from a manifest URL — and **run one `docker compose up`** to stand up the segment API + companion marking PWA. Non-expert self-host in ~5 minutes; backup is copying a file. (R02, Hard Constraint #2; [`./21-ARCHITECTURE.md`](./21-ARCHITECTURE.md))
2. Give **households per-profile filtering**: each kid's profile resolves the 9 categories × severity to skip/mute actions, with a **"request a bypass"** escape hatch (v1 = admin dashboard toggle with expiry). (R05, R06)
3. Have **contributors mark segments in the companion app** — stamp in/out points while watching, three taps, no signup — that flow into a moderation queue (vote + curator lock). (R08, R10; [`./23-CONTRIBUTION-WORKFLOWS.md`](./23-CONTRIBUTION-WORKFLOWS.md))
4. Watch the **community DB grow and mirror freely**: the whole dataset publishes as periodic public dumps; standing up a read-only mirror is a documented 5-minute task (sb-mirror pattern). (R03; [`../knowledge-base/01-working/federation-architecture.md`](../knowledge-base/01-working/federation-architecture.md))

## Honest scope of the one-year picture

- **Filtering is SKIP-only** on native clients (Web + Android TV) — Jellyfin has no mute action yet. Real profanity **mute ships via EDL export** for Kodi/mpv. We say so plainly; native mute is upstream-gated. (R07; [`./31-TRADEOFFS.md`](./31-TRADEOFFS.md))
- **Segments key to a release fingerprint** (moviehash + exact duration); when confidence is low, cleanyfin **fails safe** — "no verified data for this exact file" — rather than mis-timing a family-safety filter. (R04)
- The **server + its open dataset are the product**; the plugin and PWA are thin clients.

Next: the two feasibility spikes and the data-license call gate real code — see [`./20-ROADMAP.md`](./20-ROADMAP.md) and [`FOCUS.md`](./FOCUS.md). The longer arc is in [`./11-VISION-LONG.md`](./11-VISION-LONG.md).
