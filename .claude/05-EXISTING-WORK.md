# 05 — Existing Work & cleanyfin's Opening

> 📎 Pointer stub. Backed by [`../knowledge-base/01-working/prior-art-and-oss-competitors.md`](../knowledge-base/01-working/prior-art-and-oss-competitors.md). This is the "why does cleanyfin get to exist" file: the one direct competitor, the real standard behind it, and the four-way gap nobody has closed. For the full parts bin see [`./04-PRIOR-ART.md`](./04-PRIOR-ART.md).

## The direct competitor

**`jacob-willden/jellyfin-plugin-moviecontentfilter`** — the *only* Jellyfin-specific content-filter plugin, and almost certainly the "it definitely sucks" project that seeded this idea.

- ~17 stars, ~15 commits, GPL-3.0, C# 69% / HTML 31%.
- Self-describes as **"in very early development... many features to add (and some bugs to fix)"**; "Work in Progress" docs; **no published releases**.
- Built on the VideoSkip browser extension + Intro Skipper. Choice-based skip/mute (does not alter files — same metadata-only posture cleanyfin requires, R01).
- Consumes **local `.mcf` files / the MCF site**. Ships **no** crowdsourced DB, **no** moderation, **no** federation, **no** in-player marking. Single dev, sibling ports for Kodi and browsers.

It is the right *posture* (edit-decisions only) with almost none of the *system* around it. That is the opening — not a reason to dismiss the space, a reason to build the missing 90%.

## The standard behind it — interoperate, don't dismiss

**`delight-im/MovieContentFilter` (MCF)** is the real prior art: ~157 stars, AGPL-3.0, an open `.mcf`/WebVTT filter format + a taxonomy (violence, sex/nudity, profanity, drugs… × low/med/high, skip **or** mute) + a live crowdsourced filter site (moviecontentfilter.com, filters licensed **CC BY-NC-SA 4.0**). It explicitly positions against ClearPlay/VidAngel with "an open standard and shareable content under free licenses."

cleanyfin treats MCF as an **interop format and cold-start seed source, not a rival to erase** (R11): import/export `.mcf`, reuse the taxonomy shape, respect the ecosystem. The one real friction is licensing — CC BY-NC-SA seed data conflicts with a permissive cleanyfin DB license; resolve **before** seeding (Q40, [`./40-QUESTIONS-OPEN.md`](./40-QUESTIONS-OPEN.md)).

## cleanyfin's opening — the four things, together

No OSS project offers all four at once. That combination *is* the reason cleanyfin exists.

| # | Capability | Who has a piece | Who has all four |
|---|---|---|---|
| 1 | **Real crowdsourcing + moderation** (submit → vote → moderate → publish, account-free) | SponsorBlock (YouTube), CleanStream (Stremio), MCF site | — |
| 2 | **Federation / self-host** (offline node + public dumps + trivial mirrors; subsidiarity via curator profiles) | SponsorBlock dumps; nobody in the Jellyfin space | — |
| 3 | **Native per-profile Jellyfin enforcement + request-bypass** (Media Segments, per-viewer action, "ask for a bypass" escape hatch) | jellyfin-plugin-moviecontentfilter (no per-profile/bypass); TheIntroDB (intros only) | — |
| 4 | **Frictionless in-player marking** (stamp in/out in a few taps while watching, submit back) | segment-editor (not wired to a crowdsourced DB) | — |
| — | **All four in one system** | | **cleanyfin** |

Each capability exists in isolation (see [`./04-PRIOR-ART.md`](./04-PRIOR-ART.md)); the direct competitor has roughly one of them, weakly. cleanyfin = SponsorBlock's crowdsourcing/moderation/federation model + Jellyfin's native Media Segments enforcement + segment-editor's marking UX + MCF/EDL interop, on a boring one-`docker compose up` stack. (R02, R03, R06, R08, R09, R11)

## Honest caveats

- **Mute gap:** native clients skip only today; real mute needs the EDL bridge (Kodi/mpv) or upstream feature request [#3396](https://features.jellyfin.org/posts/3396/api-support-for-muting-media-for-content-filtering-plugin-support). (R07, [`./31-TRADEOFFS.md`](./31-TRADEOFFS.md))
- **Enforcement unproven:** whether a 10.11 plugin enforces per-profile action server-side or is client-cooperative is Spike A — gates the architecture. ([`./20-ROADMAP.md`](./20-ROADMAP.md))
- **Federation is the hardest of the four** and easy to over-build — v1 is dumps + mirrors, not S2S protocols. (R03)

## Sources

Direct-competitor and MCF detail, plus the SponsorBlock/TheIntroDB/segment-editor analogs, are cited in the backing deep-dive: [`../knowledge-base/01-working/prior-art-and-oss-competitors.md`](../knowledge-base/01-working/prior-art-and-oss-competitors.md) (findings F1, F2, F4, F5, F6; recommendation R6).
