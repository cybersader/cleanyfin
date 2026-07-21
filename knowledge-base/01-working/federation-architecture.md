# Federated, crowdsourced-database architecture (subsidiarity as a core value)

> Deep-dive from the 2026-07-21 research fan-out (workflow `cleanyfin-research`, opus, web-sourced). Lightly formatted raw findings — promote/condense into `.claude/` stubs as decisions lock. Confidence + sources preserved.

## TL;DR

SponsorBlock is the proven, load-bearing model and cleanyfin should copy it almost verbatim: one small, fully open, self-hostable submission/query API (TypeScript + Postgres/SQLite, AGPL-3.0, Docker) whose entire dataset is published as periodic public dumps that anyone can mirror incrementally (the sb-mirror pattern). That gives you "federation" in the pragmatic sense that matters for v1 — hub-and-spoke with unlimited forkable mirrors and no lock-in — without the cost of true server-to-server federation. Identity should be pseudonymous, locally-generated submitter IDs (SponsorBlock hashes a local UUID 5000x into a public ID; queries use a k-anonymity hash-prefix), with a moderation-queue-plus-voting model (auto-hide at vote score ≤ -2, curator/"VIP"-locked segments, shadowbans) rather than account walls. The single hardest and most cleanyfin-specific problem is version matching: a timestamp is worthless against the wrong rip, so segments must be keyed to a content fingerprint — use OpenSubtitles-style moviehash (filesize + first/last 64KB) plus exact runtime/duration, exactly as SponsorBlock stores videoDuration to invalidate segments when a video changes. Explicitly do NOT build ActivityPub, nostr/matrix relays, or real-time CRDT sync for v1; keep CRDTs only as an optional offline queue in the marking client, and keep Git-based dump mirroring as the natural upgrade path toward richer, curator-signed federation.

## Key Findings

### 1. SponsorBlock is a central-but-fully-open hub whose whole dataset is downloadable, making it forkable without being decentralized  ·  🟢 high

SponsorBlockServer is TypeScript/Node.js on PostgreSQL or SQLite, licensed AGPL-3.0-only, with the database published under CC BY-NC-SA 4.0. The complete dataset is exposed as ~16 CSV tables (sponsorTimes, userNames, categoryVotes, etc.) discoverable via https://sponsor.ajay.app/database.json. Sensitive fields (individual votes, hashed IPs) are withheld. This is the key architectural insight: a single logical server is fine for v1 as long as 100% of the useful data is openly dumped, because that removes lock-in — anyone can spin up a full copy or fork.

Sources: <https://github.com/ajayyy/SponsorBlockServer> · <https://sponsor.ajay.app/database>

### 2. The realistic 'federation' SponsorBlock actually uses is public dumps + independent incremental mirrors, not server-to-server protocols  ·  🟢 high

Direct CSV downloads were disabled for bandwidth reasons; users are pushed to the sb-mirror project which fetches only new data via partial HTTP range requests and self-updates without a full redownload. Independent public mirrors run at sb.ltn.fi (30 min refresh), mirror.sb.mchang.xyz (10 min), and sb.minibomba.pro (90 min); TeamPiped/sponsorblock-mirror is a Rust API+DB+sync stack. Note these mirrors are read-only query replicas — they do not accept submissions or votes. This is the pragmatic default cleanyfin should adopt for v1.

Sources: <https://sponsor.ajay.app/database> · <https://github.com/sylv/sb-mirror> · <https://github.com/TeamPiped/sponsorblock-mirror>

### 3. Queries use a hash-prefix / k-anonymity scheme so the server never learns exactly what you are watching  ·  🟢 high

Instead of GET /api/skipSegments?videoID=X, clients can call GET /api/skipSegments/:sha256HashPrefix using the first 4-32 characters (4 recommended) of the SHA-256 of the videoID; the server returns segments for ALL videos sharing that prefix and the client filters locally. This is directly relevant to cleanyfin, where the query itself ('someone is watching Movie Y') is privacy-sensitive. Submitter identity is likewise pseudonymous: the public userID is the local userID hashed with SHA-256 5000 times, so no account or email is required to contribute.

Sources: <https://github.com/ajayyy/SponsorBlock/wiki/K-Anonymity> · <https://wiki.sponsor.ajay.app/w/API_Docs>

### 4. Moderation is a layered vote + curator + shadowban system, not hard account gates  ·  🟢 high

Submissions POST to /api/skipSegments (fields: videoID, userID, segment[start,end], category, actionType, videoDuration); votes POST to /api/voteOnSponsorTime (UUID, userID, type 0=down/1=up, or a category-reclassification vote). Quality control: a segment auto-hides for everyone at vote score ≤ -2 (still retained in DB). 'VIP' curators (earned after ~1 month of good submissions) cast weighted votes — a VIP downvote removes instantly, and VIP-submitted/upvoted segments become 'locked', always shown over competing unlocked segments and removable only by other VIPs. Vandals are shadowbanned: their segments remain visible only to their own originating IP, silently hidden from everyone else, and the ban auto-applies to all future submissions. A weighted-random display formula gives new segments exposure so they can accrue votes.

Sources: <https://github.com/ajayyy/SponsorBlock/wiki/FAQ/a9b70cd9e74993fb7b31f835ef104b1e3623e26a> · <https://wiki.sponsor.ajay.app/w/VIP> · <https://wiki.sponsor.ajay.app/w/API_Docs>

### 5. Version/edition matching is the make-or-break problem, and OpenSubtitles moviehash is the proven, cheap solution  ·  🟢 high

Different rips/cuts of the same title have different frame offsets, intros, and runtimes, so a segment [00:12:03–00:12:29] is only correct against the exact file it was authored on. OpenSubtitles solved the identical problem for subtitles with 'moviehash' (aka OSHash): a 16-hex-char hash = filesize + checksum of first 64KB + checksum of last 64KB, computed without reading the whole file (fast on multi-GB files; valid for 131072 < size < 9e9 bytes). Weakness: it is a speed hash, collides on same-size/same-ends files, and changes if the file is re-muxed. Pair it with exact runtime/duration as a secondary key — SponsorBlock already stores videoDuration per segment specifically to detect when a video has changed and invalidate stale segments. cleanyfin should key every segment set to (contentFingerprint, duration) and treat mismatches as 'no data for this exact file'.

Sources: <https://opensubtitles.github.io/oshash/> · <https://github.com/opensubtitlescli/moviehash> · <https://wiki.sponsor.ajay.app/w/API_Docs>

### 6. Audio fingerprinting (Chromaprint/AcoustID) can map timestamps across differently-offset rips but is a v2+ enhancement, not a v1 requirement  ·  🟡 medium

Chromaprint (the engine behind AcoustID, LGPL) extracts compact fingerprints optimized for near-identical audio and, with per-fingerprint timestamps, can compute the time offset between a reference and a target stream — in principle letting a segment authored on rip A auto-align onto rip B. But Chromaprint explicitly trades robustness for search speed, AcoustID typically matches only the first 25-40s, and full offset-mapping across arbitrary cuts is genuinely hard. For v1, exact-file matching via moviehash+duration is dramatically simpler and 'good enough'; treat audio-fingerprint offset transfer as a later, opt-in resiliency layer that widens coverage.

Sources: <https://github.com/acoustid/chromaprint> · <https://acoustid.org/chromaprint> · <https://groups.google.com/g/acoustid/c/C0QPEqkkpxk>

### 7. True server-to-server federation (ActivityPub) buys little for a timestamp DB and imports heavy moderation cost  ·  🟢 high

ActivityPub gives independent instances that each enforce their own rules and can defederate, but studies of Mastodon document that this produces fragmented, inconsistent moderation, duplicated effort, and interoperability friction across implementations — overhead aimed at a social-graph/messaging problem, not a small append-mostly dataset of timestamps. For cleanyfin, the 'different communities disagree about what to filter' requirement is better met by curator profiles inside one open dataset than by standing up a fediverse. Forgejo/Gitea are adding ActivityPub repo federation, but that is bleeding-edge and unnecessary for v1.

Sources: <https://carnegieendowment.org/research/2025/03/fediverse-social-media-internet-defederation?lang=en> · <https://policyreview.info/articles/analysis/content-moderation-challenges>

### 8. CRDTs (Automerge 3.0 / Yjs) are the right tool ONLY for the offline marking queue, not for the shared DB  ·  🟡 medium

Yjs is the most mature CRDT lib; Automerge 3.0 (May 2025) cut memory ~10x with a Rust core and stable JS API, and Yjs+IndexedDB enables fully offline edits that merge conflict-free on reconnect — ideal for a 'mark segments while watching offline, sync later' client. But CRDTs solve concurrent-multi-writer merge, whereas cleanyfin's shared DB needs moderation/voting/dedup that CRDTs do not provide, and their metadata overhead is wasted on a moderated append log. Use a CRDT (or just a simple local outbox table) for the client's pending submissions; keep the authoritative shared DB a plain moderated store.

Sources: <https://stack.convex.dev/automerge-and-convex> · <https://dev.to/hexshift/building-offline-first-collaborative-editors-with-crdts-and-indexeddb-no-backend-needed-4p7l>

### 9. Git-based federation of the data dumps is the strongest low-cost upgrade path and a natural home for curator trust circles  ·  🟡 medium

Publishing segment bundles as files in a Git repo makes the whole corpus auditable (history cannot be silently rewritten), forkable, PR-reviewable, and serverless to host (any static host / GitHub / Forgejo mirror). The known downside — poor real-time marking UX — does not apply to distribution, only to live submission, which the API server already handles. So the clean split is: live submission via the API server; durable, forkable distribution via signed Git-hosted dumps. A curator is then just a repo/branch or a signed bundle that families subscribe to, giving subsidiarity (local-first, opt-in publish/pull, coexisting community norms) without new infrastructure.

Sources: <https://en.wikipedia.org/wiki/Fork_and_pull_model> · <https://mickaelruau.medium.com/git-repos-federation-a-viable-alternative-to-blockchains-89b9d4ef3a83>

### 10. Nostr and Matrix are honest long-shots and should be ruled out for v1  ·  🟡 medium

Nostr's model — cryptographically signed events under a user pubkey, pushed to many chosen relays — maps appealingly onto 'signed segment contributions' and pseudonymous identity, and its no-single-point-of-failure resilience is real. But relays are designed to expire/drop events after a retention window, which is disqualifying for a durable reference DB, and you would rebuild all moderation/dedup/version-keying yourself on top. Matrix ties a user to a single homeserver and is even heavier. Signed-contribution ideas from Nostr (per-curator keypair signing bundles) are worth borrowing; the relay transport is not.

Sources: <https://voltage.cloud/blog/understanding-nostr-data-storage-relays-and-decentralization> · <https://en.wikipedia.org/wiki/Nostr>

## Recommendations for cleanyfin

**R1. Ship v1 as a single self-hostable SponsorBlock-clone API: one small TypeScript/Go/Python service over SQLite (upgradeable to Postgres), AGPL-3.0 code + an open dataset license, packaged as a one-command Docker Compose. Expose GET (with hash-prefix variant), POST submit, and POST vote endpoints. Publish the ENTIRE dataset as periodic public dumps from day one.**

- *Why:* This is the proven, minimal, resilient path — SponsorBlock runs a global crowdsourced timestamp DB on exactly this stack. SQLite-first honors the 'super-easy self-host' constraint (no external DB to operate); open dumps deliver real anti-lock-in 'federation' immediately without protocol complexity. It distributes only timestamps/metadata, keeping it DMCA-safe like SponsorBlock.
- *Risk / tradeoff:* A logically central submission endpoint is a single point of failure for WRITES and a potential legal/abuse target. Mitigate by making dumps frequent and mirrors trivial (so reads survive any outage) and by designing the schema so a fork can become the new hub with zero data loss.

**R2. Make the 'sb-mirror' pattern a first-class, documented feature: a versioned dump manifest (JSON index of tables + last-updated), incremental HTTP range-based sync, and a 5-minute 'stand up a read-only mirror' guide. Treat mirrors as the federation story for v1.**

- *Why:* Cheap mirrors are what actually delivers subsidiarity and resilience today: any household/community can hold a full local copy that works fully offline, and the network survives the main node dying. It is far simpler than ActivityPub and already battle-tested by multiple independent SponsorBlock mirrors.
- *Risk / tradeoff:* Mirrors are read replicas — they don't accept submissions, so contribution still funnels to the hub. Accept this for v1; the Git-dump upgrade path (below) later lets mirrors also publish local contributions upstream via PR.

**R3. Key every segment set to a content fingerprint, not a title string: primary key = OpenSubtitles moviehash (filesize + first/last 64KB) plus exact runtime/duration; store human title/year only as searchable metadata. Refuse to apply segments whose fingerprint/duration doesn't match the local file, and surface 'no verified data for this exact file'.**

- *Why:* This is the single most cleanyfin-specific correctness risk: applying the wrong rip's timestamps produces visibly broken filtering and destroys user trust. moviehash is fast (no full-file read), proven at scale by OpenSubtitles, and cheap to compute client-side. Duration acts as SponsorBlock's videoDuration invalidation signal.
- *Risk / tradeoff:* moviehash collides on same-size files and breaks on re-mux/transcode, so coverage is per-exact-file and can be sparse. Plan a v2 audio-fingerprint (Chromaprint) offset-transfer layer to generalize a segment across rips, but don't block v1 on it.

**R4. Adopt account-free pseudonymous identity + a moderation queue, not gates: locally-generated UUID hashed into a public submitter ID, k-anonymity hash-prefix for queries, weighted-random exposure for new segments, auto-hide at vote score ≤ -2, silent shadowbans for vandals, and 'curator'-locked segments that win over unlocked ones.**

- *Why:* Matches the maintainer's stated dislike of forced accounts while still giving real spam/vandalism resistance — this exact recipe keeps SponsorBlock usable at millions of segments. Pseudonymous signed contributions let reputation accrue without collecting PII.
- *Risk / tradeoff:* Pseudonymous IDs enable sockpuppet/Sybil vote manipulation; rate-limit by IP (429) and weight votes by curator trust. Shadowbans and instant curator-removal concentrate power in curators — make curator lists themselves forkable so no single moderator is a chokepoint.

**R5. Model 'trust circles' as subscribable curator profiles inside the one open dataset, not as separate servers: a family follows one or more curators whose standards they share; conflicting community norms coexist as competing/overlapping segment+category sets rather than one global truth. Let a profile pin a curator's locked segments.**

- *Why:* This is how you honor subsidiarity and 'different communities filter differently' WITHOUT ActivityPub or multiple instances. It generalizes SponsorBlock's single VIP-lock layer into N parallel curator layers, which is a small schema change (a curator/namespace column + a subscription list) rather than new infrastructure.
- *Risk / tradeoff:* Too many overlapping curator layers can confuse users about 'why was this skipped'. Keep a clear precedence rule (subscribed-curator-locked > community-voted > unmoderated) and show provenance per applied edit.

**R6. Explicitly DEFER for v1: ActivityPub server-to-server federation, nostr/matrix relays, and real-time CRDT sync of the shared DB. Use a CRDT or a plain local outbox ONLY inside the marking client for offline-captured submissions that sync when online.**

- *Why:* Each of these solves a problem cleanyfin doesn't yet have and imports large operational/moderation cost (fediverse defederation fragmentation; nostr event expiry; CRDT overhead on a moderated append log). Cutting them is the difference between 'super-easy to self-host' and a distributed-systems project.
- *Risk / tradeoff:* Deferring true federation could disappoint decentralization purists. Counter it by committing publicly to the Git-dump upgrade path so the openness promise is credible without building the heavy machinery now.

**R7. Design the upgrade path now: define the dump format as signed, curator-scoped bundles that can live in a Git repo (fork/PR/pull). This later turns mirrors into two-way participants and makes curators cryptographically verifiable, borrowing nostr-style signed contributions without adopting relays.**

- *Why:* Git-hosted signed bundles give auditable, tamper-evident, serverless-to-host distribution and are the lowest-cost route from hub-and-spoke to genuine multi-party federation. Choosing a signable, file-friendly dump schema in v1 (stable IDs, per-record signatures, curator pubkeys) costs almost nothing and avoids a painful migration later.
- *Risk / tradeoff:* If v1's schema isn't designed with signing/bundling in mind, retrofitting signatures and stable content-addressed IDs later is disruptive. Spend a little design effort up front on stable segment IDs and an optional signature column.

## Open Questions

- **What is the canonical content fingerprint — moviehash (filesize + first/last 64KB) alone, moviehash + exact duration, or add a Chromaprint audio fingerprint for cross-rip transfer?** — *lean:* v1: moviehash + exact duration as the match key (exact-file only). Defer Chromaprint audio-offset transfer to v2 as an opt-in coverage-widening layer.
- **Should segments be authored in absolute file timestamps, or as offsets anchored to a detected reference point (e.g., first frame after intro) to survive minor re-encodes?** — *lean:* Absolute timestamps against a specific fingerprint for v1 (simplest, correct-by-construction); anchor-relative offsets only as part of the later audio-fingerprint version-bridging work.
- **What licenses for code vs. data? SponsorBlock uses AGPL-3.0 code + CC BY-NC-SA 4.0 data.** — *lean:* Mirror SponsorBlock: AGPL-3.0 (or a permissive alt if you want easy client integration) for code; a CC BY-SA-style share-alike for the dataset so mirrors/forks stay open. Confirm NC vs not with the DMCA/licensing research dimension.
- **How are curators bootstrapped and trusted before a reputation history exists — self-declared keypairs, maintainer-blessed initial set, or web-of-trust endorsements?** — *lean:* Start with a small maintainer-blessed curator set plus open self-declared curators that users can subscribe to at their own risk; add lightweight endorsement/web-of-trust later.
- **Do read-only mirrors ever accept local submissions, and if so how do they flow back to the hub without central write dependence?** — *lean:* v1 mirrors stay read-only; contributions go to the hub. Introduce upstream contribution via signed Git bundles / PRs in the federation-upgrade phase rather than building multi-master write sync now.
- **How aggressively to rate-limit and Sybil-defend given account-free identity — IP-based only, proof-of-work on submit, or curator-weighted trust?** — *lean:* IP rate limits + vote-score auto-hide + shadowbans for v1 (SponsorBlock's proven set); reserve proof-of-work/curator-weighting for if/when abuse actually appears.

## Sources

- [SponsorBlockServer (GitHub)](https://github.com/ajayyy/SponsorBlockServer) — Reference implementation: TypeScript/Node, Postgres or SQLite, AGPL-3.0 code + CC BY-NC-SA 4.0 data, Docker deploy — the exact stack to clone for cleanyfin's v1 hub.
- [SponsorBlock Database Dumps](https://sponsor.ajay.app/database) — Shows the ~16-table public dump model, database.json manifest, CC BY-NC-SA 4.0 data license, and why direct CSV was replaced by incremental mirroring.
- [sb-mirror (GitHub)](https://github.com/sylv/sb-mirror) — The incremental, bandwidth-friendly mirror pattern (partial HTTP requests, self-updating) that is cleanyfin's pragmatic 'federation' for v1.
- [TeamPiped/sponsorblock-mirror (GitHub)](https://github.com/TeamPiped/sponsorblock-mirror) — Rust API+DB+sync mirror stack proving independent, forkable read-replicas of a crowdsourced timestamp DB are viable.
- [SponsorBlock API Docs (wiki)](https://wiki.sponsor.ajay.app/w/API_Docs) — Endpoint + field reference: skipSegments GET/hash-prefix, POST submit, voteOnSponsorTime, votes/locked/hidden/shadowHidden/reputation, videoDuration, 429/409 handling.
- [SponsorBlock K-Anonymity (wiki)](https://github.com/ajayyy/SponsorBlock/wiki/K-Anonymity) — How hash-prefix queries hide which title a user is watching — directly applicable to cleanyfin's privacy-sensitive lookups.
- [SponsorBlock FAQ — moderation/vandalism (wiki)](https://github.com/ajayyy/SponsorBlock/wiki/FAQ/a9b70cd9e74993fb7b31f835ef104b1e3623e26a) — Vote score ≤ -2 auto-hide, shadowbans, and quality-control mechanics for a crowdsourced segment DB.
- [SponsorBlock VIP Guide (wiki)](https://wiki.sponsor.ajay.app/w/VIP) — Curator/'VIP' model: weighted votes, instant removal, locked segments — the seed of cleanyfin's subscribable curator/trust-circle design.
- [OpenSubtitles Hash (OSHash/moviehash) reference](https://opensubtitles.github.io/oshash/) — The exact version-matching algorithm to reuse: filesize + first/last 64KB, fast on large files, maps metadata to the RIGHT file rip.
- [opensubtitlescli/moviehash (GitHub)](https://github.com/opensubtitlescli/moviehash) — Maintained implementation + explicit limitations (speed hash, collisions, not for integrity) informing when to add a secondary fingerprint.
- [Chromaprint / AcoustID](https://github.com/acoustid/chromaprint) — LGPL audio-fingerprint engine for the v2 cross-rip offset-transfer layer; note its near-identical-audio and 25-40s matching limits.
- [Automerge + Convex: going local-first](https://stack.convex.dev/automerge-and-convex) — Automerge 3.0 (May 2025) maturity for offline-first — scoping CRDTs to the marking client's offline queue, not the shared DB.
- [Offline-first collaborative editing with Yjs + IndexedDB](https://dev.to/hexshift/building-offline-first-collaborative-editors-with-crdts-and-indexeddb-no-backend-needed-4p7l) — Concrete pattern for capture-offline-sync-later marking UX without a backend, using Yjs+IndexedDB.
- [Carnegie: Defederation on decentralized social media](https://carnegieendowment.org/research/2025/03/fediverse-social-media-internet-defederation?lang=en) — Evidence that ActivityPub-style S2S federation imports fragmented, costly moderation — why it is over-engineering for cleanyfin v1.
- [Understanding Nostr Data Storage & Decentralization (Voltage)](https://voltage.cloud/blog/understanding-nostr-data-storage-relays-and-decentralization) — Signed-events/pubkey identity worth borrowing, but relay event-expiry disqualifies nostr as the durable transport for v1.
- [Fork and pull model (Wikipedia)](https://en.wikipedia.org/wiki/Fork_and_pull_model) — Baseline for the Git-based dump-federation upgrade path: auditable, forkable, PR-reviewed distribution with no server.
