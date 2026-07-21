# 02 — The Ecosystem

> 📎 Pointer stub. Backed by [`../knowledge-base/01-working/prior-art-and-oss-competitors.md`](../knowledge-base/01-working/prior-art-and-oss-competitors.md) and [`../knowledge-base/01-working/jellyfin-integration-mechanics.md`](../knowledge-base/01-working/jellyfin-integration-mechanics.md). Deeper per-project notes live in [`04-PRIOR-ART`](./04-PRIOR-ART.md) and [`05-EXISTING-WORK`](./05-EXISTING-WORK.md).

cleanyfin is not inventing a category — it is assembling proven pieces that already exist *separately*. This is the "who's who + where we fit" map.

## The two load-bearing foundations

- **Jellyfin — the host we build ON (don't fork).** Native **Media Segments** landed in server 10.10 (Nov 2024) and matured in 10.11. A segment = `ItemId + StreamIndex + Type + StartTicks/EndTicks + Action`; **plugins** supply segments via `IMediaSegmentProvider`, and clients render the skip UI. cleanyfin registers as a standard segment provider (R02). Two structural facts to design around: (1) the **type enum is fixed** (Intro/Outro/Recap/Preview/Commercial/Unknown — no "Profanity"/"Nudity"), so cleanyfin carries its own category taxonomy in its DB and translates at emit time; (2) **client support is uneven** — Web + Android TV 0.18+ do skip today; Swiftfin (iOS/tvOS) and webOS lag; **no native mute action** exists yet, so MVP is skip-only with EDL for real mute (R07). Segments are also **global per item, not per-profile** — cleanyfin builds its own profile→category layer.
- **SponsorBlock — the data MODEL we clone.** The reference architecture for crowdsourced timestamps: community submission, up/down voting, category re-voting, VIP/submitter moderation overrides, a **public downloadable DB dump**, and a **hash-prefix privacy query**. cleanyfin copies these mechanics wholesale (R08, R03), scoped to `(title/version + category)` instead of YouTube IDs. Its DB is **CC BY-NC-SA 4.0** — a license cleanyfin must *not* reuse, and a deliberate own-license choice it must make (Q40).

## Product reference points (the feature bar)

| Product | What it is | Take-away for cleanyfin |
|---|---|---|
| **ClearPlay** | Commercial, edit-list applied to your own player; ~14 categories; ~20 yrs legal | The FMA-safe architecture we emulate; the curated-catalog bar |
| **VidAngel** | Commercial; post-lawsuit filters over your own Netflix/Prime/etc. streams | Cautionary tale (DRM+copies lost); word-level mute is the UX to aspire to |
| **TVGuardian** | Hardware/CC audio filter; 150+ word dictionary; mutes + shows cleaned caption; no streaming | The sentence-level profanity-mute trick OSS hasn't matched |

## Where we fit — the landscape map

```
              CROWDSOURCED DATA MODEL  ───────────────►
              (submit / vote / moderate / public dump)
        low                                         high
   ┌───────────────────────────┬──────────────────────────────┐
 J │  jacob-willden MCF plugin  │        ✦ cleanyfin ✦          │  Jellyfin-
 E │  (early, local .mcf,       │  (crowdsourced + federated +  │  native
 L │   no crowdsourcing)        │   per-profile + in-player mark)│
 L ├───────────────────────────┼──────────────────────────────┤
 Y │  TheIntroDB (intros only)  │  SponsorBlock (YouTube ads,   │  adjacent /
 F │  Intro Skipper (auto-detect)│  not content-filtering)      │  other host
 I └───────────────────────────┴──────────────────────────────┘
   Adjacent hosts: Kodi + EDL · Plex + PlexAutoSkip · Stremio CleanStream
```

## Adjacent / interop players

- **Kodi + EDL** — the simplest, oldest, most portable edit-decision format (`start end action`; 0=cut,1=mute,2=scene,3=commercial). **EDL is our real mute path** for Kodi/mpv and a first-class import/export target (R11).
- **Plex + PlexAutoSkip** — closest Plex analog: server-side Python auto-skip/mute of markers, but **in maintenance mode, no shared DB**. Shows the single-instance, non-crowdsourced ceiling.
- **Movie Content Filter (MCF)** — the open `.mcf`/WebVTT standard + taxonomy (`delight-im`) and its crowdsourced site. Real prior art to **interoperate with** (import/export), not dismiss (R11). Its Jellyfin port is the weak direct competitor above.
- **Stremio CleanStream** — MCF-compatible crowdsourced addon; validates the model *and* that auto-skip is hard (it ships warnings first).
- **cleanvid / monkeyplug** — subtitle- and speech-based profanity detection that emit EDL/JSON edit-decisions; **seed automation** for the Language category (suggestion-only, human-gated — R10).
- **Intro Skipper + segment-editor** — the flagship `IMediaSegmentProvider` to clone, and an existing in-player "copy timestamps while you watch" marking UI to borrow for the companion PWA.

## Where cleanyfin sits (the gap)

Every existing OSS project has *some* of the pieces; **none has all four together**: (1) real crowdsourcing + moderation, (2) federation / self-host, (3) native per-profile Jellyfin enforcement + request-bypass, (4) frictionless in-player marking. That four-way intersection — SponsorBlock's data model + VidAngel's experience, on Jellyfin, metadata-only — is cleanyfin's whole reason to exist. See [`21-ARCHITECTURE`](./21-ARCHITECTURE.md) for how the plugin + server + PWA realize it, and [`05-EXISTING-WORK`](./05-EXISTING-WORK.md) for the head-to-head.

## Sources

- Jellyfin Media Segments — <https://jellyfin.org/docs/general/server/metadata/media-segments/>
- SponsorBlock — <https://github.com/ajayyy/SponsorBlock> · API — <https://wiki.sponsor.ajay.app/w/API_Docs>
- MCF standard — <https://github.com/delight-im/MovieContentFilter> · Jellyfin port — <https://github.com/jacob-willden/jellyfin-plugin-moviecontentfilter>
- Kodi EDL — <https://kodi.wiki/view/Edit_decision_list> · PlexAutoSkip — <https://github.com/mdhiggins/PlexAutoSkip>
- cleanvid — <https://github.com/mmguero/cleanvid> · CleanStream — <https://github.com/ameen-roayan/stremio-cleanstream>
