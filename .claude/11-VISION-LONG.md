# cleanyfin — Vision (Long)

> 📎 Pointer stub — the longer arc, from single-node self-host to a curator-signed federation, and an explicit out-of-scope list. Synthesized from all six deep-dives; leans on [`../knowledge-base/01-working/federation-architecture.md`](../knowledge-base/01-working/federation-architecture.md), [`../knowledge-base/01-working/tech-stack-and-devops.md`](../knowledge-base/01-working/tech-stack-and-devops.md), and [`../knowledge-base/01-working/legal-and-ip-landscape.md`](../knowledge-base/01-working/legal-and-ip-landscape.md). Short version: [`./10-VISION-SHORT.md`](./10-VISION-SHORT.md).

## The arc

The whole point is that the **openness promise stays credible without ever building heavy distributed-systems machinery**. Each stage delivers a real property; nothing later stage requires re-doing an earlier one.

```
Stage 1  Single self-host node        one docker compose up; SQLite file; skip on Web/AndroidTV; EDL mute
Stage 2  Mirror network               public dumps + trivial read-only mirrors (sb-mirror); offline/anti-lock-in
Stage 3  Curator federation           signed Git-bundle dumps; per-curator keypairs; fork/PR; subsidiarity realized
Stage 4  Native mute                   when upstream Jellyfin ships a mute action, drop it in (no re-architecture)
Stage 5  Cross-rip alignment           optional Chromaprint audio-anchor auto-align; one segment covers many rips
Stage 6  Broader interop               Kodi/EDL round-trip, MCF import/export, other servers/players
```

### Stage 1 — Single-node self-host (v1)
Thin C# `IMediaSegmentProvider` plugin + a small self-hostable Go API (single static binary embedding the PWA, `modernc.org/sqlite`, SQLite-WAL) + the marking PWA. One `docker compose up` or a binary + systemd. This is the whole product on day one. (R02; [`./21-ARCHITECTURE.md`](./21-ARCHITECTURE.md))

### Stage 2 — Mirror network
Publish the **entire dataset as periodic public dumps** from day one; make read-only mirrors a first-class, documented feature (incremental HTTP range sync, sb-mirror pattern). A household holds a full local copy that works fully offline; reads survive the hub dying. This *is* the v1 "federation." (R03; [`../knowledge-base/01-working/federation-architecture.md`](../knowledge-base/01-working/federation-architecture.md) R1–R2)

### Stage 3 — Signed-Git-bundle curator federation (subsidiarity realized)
Define the dump format now as **signed, curator-scoped bundles** that can live in a Git repo (fork / PR / pull). A curator becomes a repo/branch or a signed bundle families subscribe to; conflicting community norms coexist as competing segment sets with a clear precedence rule (subscribed-curator-locked > community-voted > unmoderated). Live submission stays on the API server; durable distribution moves to auditable, tamper-evident Git — borrowing nostr-style signed contributions without adopting relays. (R09; [`../knowledge-base/01-working/federation-architecture.md`](../knowledge-base/01-working/federation-architecture.md) R7)

### Stage 4 — Native mute
Today native Jellyfin clients only skip. When upstream ships a mute action, cleanyfin's default category→action map (profanity/sexual_dialogue/crude → mute) lights up on Web/Android TV with no schema change. We **track upstream, don't hack playback**. (R07; [`./31-TRADEOFFS.md`](./31-TRADEOFFS.md))

### Stage 5 — Optional Chromaprint cross-rip alignment
v1 keys every segment to `(moviehash + exact duration)` = exact-file only, which is correct-by-construction but coverage is sparse. A later **opt-in** Chromaprint audio-anchor layer computes the offset between rips so one authored segment auto-aligns across editions. It stays opt-in and fail-safe — never silently mis-time a family filter. (R04; [`../knowledge-base/01-working/federation-architecture.md`](../knowledge-base/01-working/federation-architecture.md) F6)

### Stage 6 — Broader interop
First-class **MCF (.mcf/WebVTT) + Kodi EDL import/export** so cleanyfin interoperates with the existing MovieContentFilter ecosystem, PlexAutoSkip, Stremio CleanStream, and can seed a non-empty DB — then reach toward other servers/players. (R11; [`./04-PRIOR-ART.md`](./04-PRIOR-ART.md))

## Explicitly OUT of scope (permanent non-goals)

| Non-goal | Why |
|---|---|
| **Hosting / caching / transcoding / exporting media** | The legal keystone. Metadata only, forever. An "export a filtered MP4" feature forfeits Family-Movie-Act protection. (R01) |
| **DRM circumvention** (rip-your-disc helpers, decrypting streams) | DMCA §1201 is strict-liability; it destroyed VidAngel independent of infringement. cleanyfin never touches a TPM. (R01) |
| **Hyperscalers / Kubernetes** | Self-host is the mission. Single-node resilience via `restart: unless-stopped` + healthcheck + file/Litestream backup — no AWS/GCP/k8s. |
| **Forced accounts** | Contribution safety = pseudonymous submitter IDs + moderation, never an account wall. (R08) |
| **Forking Jellyfin or its clients** | Build on native Media Segments; ride the native skip UI; contribute upstream instead. |
| **Heavy S2S protocols** (ActivityPub / nostr / matrix / shared-DB CRDTs) | Mirrors + signed Git dumps deliver subsidiarity at a fraction of the cost. Deferred, not planned. (R03) |
| **Filtering commercial DRM streams** (VidAngel's pivot model) | Only the user's own Jellyfin library files — the cleanest, most defensible scope. |

Reality checks: the FMA safe harbor is **US-only** (no EU/UK equivalent) — federation stays jurisdiction-aware and nodes bear their own local risk. A **freedom-to-operate review** of ClearPlay's post-2015 patents belongs before any funded launch. (See [`../knowledge-base/01-working/legal-and-ip-landscape.md`](../knowledge-base/01-working/legal-and-ip-landscape.md) and [`./40-QUESTIONS-OPEN.md`](./40-QUESTIONS-OPEN.md).)
