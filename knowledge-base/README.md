# knowledge-base

The working research corpus for cleanyfin, organized as a **temperature gradient** (hot/raw → cool/settled). See [`.claude/KNOWLEDGE_BASE_PHILOSOPHY.md`](../.claude/KNOWLEDGE_BASE_PHILOSOPHY.md).

| Folder | Temperature | Holds |
|---|---|---|
| [`00-inbox/`](./00-inbox/) | 🔥 hot | Raw captures, quick notes, unprocessed dumps |
| [`01-working/`](./01-working/) | 🌤️ warm | Actively-developed docs, deep dives, synthesis — **the current canonical depth** |
| [`04-archive/`](./04-archive/) | ❄️ cold | Superseded / historical material kept for provenance |

## Current deep-dives (`01-working/`)

From the 2026-07-21 research fan-out (six parallel agents, web-sourced, cited):

- [`legal-and-ip-landscape.md`](./01-working/legal-and-ip-landscape.md) — Family Movie Act, VidAngel vs ClearPlay, DMCA §1201, patent landscape, the metadata-only rule.
- [`prior-art-and-oss-competitors.md`](./01-working/prior-art-and-oss-competitors.md) — the competitor, SponsorBlock, Intro Skipper, MCF, cleanvid, EDL, and cleanyfin's opening.
- [`jellyfin-integration-mechanics.md`](./01-working/jellyfin-integration-mechanics.md) — Media Segments API, provider plugins, skip vs mute reality, client support, the marking path.
- [`federation-architecture.md`](./01-working/federation-architecture.md) — SponsorBlock model, dumps + mirrors, moviehash version-matching, moderation, subsidiarity via curators.
- [`tech-stack-and-devops.md`](./01-working/tech-stack-and-devops.md) — Go single-binary + SQLite + Litestream, one docker-compose, the plugin CI/CD path.
- [`tagging-taxonomy-and-data-model.md`](./01-working/tagging-taxonomy-and-data-model.md) — the fixed-9 category taxonomy, segment schema, calibration, profile/bypass model.

### Feasibility spikes (2026-07-21, source-verified vs Jellyfin 10.11)

- [`spike-a-enforcement.md`](./01-working/spike-a-enforcement.md) — no per-user seam in the segment pipeline; the trustworthy path to per-profile enforcement is cleanyfin's own opt-in response-filtering proxy (→ R13).
- [`spike-b-segment-write-api.md`](./01-working/spike-b-segment-write-api.md) — no core write endpoint; ship our own thin controller, PWA writes to our Go API; DTO is `Id/ItemId/Type/StartTicks/EndTicks` only (→ R14).
- [`spike-c-client-support.md`](./01-working/spike-c-client-support.md) — skip fleet is wider than assumed (Web/Android TV/Roku/Kodi); native mute still absent everywhere (→ R07).

These are condensed into the orientation stubs under [`.claude/`](../.claude/00-INDEX.md).
