# A small, recoverable filtering ecosystem

Research access: **2026-10-08**. Folder date preserves continuity. **PROPOSED architecture; only product sequencing below is owner-approved, not the architecture or production readiness.** Revised against the [independent review](06-independent-review.md); companion [roadmap](05-roadmap-and-experiments.md).

## Proposed direction and owner sequencing

Build the durable asset first: portable annotations with household-local authority, not a distributed playback platform. Retain one Go/SQLite application, one deliberately supported cooperative playback adapter and origin-owned curator snapshots. Simplicity and manageable maintenance outweigh speculative infrastructure.

**Owner sequencing decision, 2026-10-08:** dependable filtering on explicitly supported cooperative clients is the first-release target. Server-side enforcement/deliberate-bypass resistance remains a LONGER-TERM product objective, not abandoned and not a first-release prerequisite. Owner response: “Yep but keep server-side enforcement on the longer-term roadmap”. This accepts sequencing only, not the surrounding architecture or an implementation technique.

The [retained roadmap track](05-roadmap-and-experiments.md) requires an explicit threat model and feasibility spike addressing original-media access, alternate clients, downloads, file shares and authorization, client/format compatibility and operating cost; reliable failure/recovery and applicable legal review evidence must precede enforcement claims. Metadata filtering or remote commands alone are not unbypassable playback enforcement. Scope/design remain open; no enforceability against a server administrator or someone controlling the media is claimed. Sequencing grants no DRM/circumvention scope or legal clearance.

This is a defensible development direction, not a feasibility certificate. Current native Unknown delivery and refresh degradation block dependable production-protection claims, **not separately authorized isolated experiments**. No experiment or implementation is authorized by this proposal or review.

## Evidence and present boundaries

**IMPLEMENTED** means present in source, not proven in operation. **SOURCE-VERIFIED** means supported by identified source/version/documentation. **PROPOSED** means recommended but unimplemented/unapproved. **UNVERIFIED** means required evidence is missing. **REJECTED** denotes a claim rejected by this analysis, not an invented owner decision.

Repository baseline: `2426cef1ab345a5a829b2ce7a6fe8fab8661011e`.

- **IMPLEMENTED:** `plugin/CleanyfinSegmentProvider.cs:48-92` queries a fingerprint, emits every span as Unknown, discards action/category and catches fetch failures into empty output. Submitted mute/mark is not a native mute/mark instruction.
- **SOURCE-VERIFIED:** Web v10.11.11 defaults Unknown to None and requests enabled types only [S1–S2]. Nonzero-start automatic skips shorter than one second and prompts shorter than three seconds under analogous nonzero start/end guards can be suppressed. Previous-position logic can suppress replayed action. Configured Unknown is not proven impossible; its actual configuration/lifecycle/output requires E1. Convenience-player semantics are not sample-accurate exclusion.
- **SOURCE-VERIFIED conditional inference:** ordinary server refresh replaces changed provider output [S3]. Deletion can follow a caught API failure when an enabled supporting provider actually runs with existing provider rows, returns empty, and deletion remains executable with a usable parent cancellation token/database. No-refresh, disabled-provider or cancelled-parent cases must not be silently collapsed into this scenario. Force-overwrite deletes all item segments before providers execute; replacement inserts follow deletion and can fail partway. This affects Jellyfin materialization, not necessarily canonical API annotations. No live reproduction or preservation fix is established.
- **SOURCE-supported conditional hypothesis, runtime UNVERIFIED:** Web's manager stores asynchronous fetch results without source/request correlation and does not clear the stored array on its inspected start path [S2]. If one manager instance spans A/B, late A results or retained A data might affect B. Lifecycle reuse and actual misapplication were not verified. E1/E5 must delay/reorder results and inspect output. Do not publish this as a reproduced bug or exploit.
- **IMPLEMENTED:** `server/internal/store/store.go:139-184,201-255` creates pending rows yet returns non-hidden rows above the vote threshold, and keys votes by claimed submitter strings. `server/internal/api/api.go:35-47,191-249` has no application write-authentication gate. Pending is public visibility, not quarantine; one string is not one authenticated contributor or human.
- **IMPLEMENTED:** `plugin/Moviehash.cs:22-66`, provider `:54-80` and store `:158-161` implement sampled OSHash/local fallback and fingerprint-only lookup. Duration/source equivalence is not verified.
- **IMPLEMENTED:** `plugin/SegmentsController.cs:45-99` forwards a submission; `pwa/src/main.ts:50-99` polls the first playing session. Neither proves immediate materialization or precise deliberately selected-player marking.
- **IMPLEMENTED:** store `:69-122,201-222` limits each Store to one connection, serializing reads and writes; discards an ALTER error; collects visible dumps in memory. These are not capacity results.

Session seek and Mute/Unmute commands exist [S7–S8]. Dispatch is not an interval scheduler, timing guarantee or proof of device compliance. Custom-controller source raises per-item authorization questions; E5 must verify behavior rather than asserting an exploit.

## One application, explicit trust boundaries

**IMPLEMENTED components:** Go API/SQLite, Jellyfin provider/forwarding controller and separate marking PWA. Existing Jellyfin serves household media. **PROPOSED additions within the same application:** publication, import, local policy, versioned plans, outbox and recovery tooling. No seven-service split is implied.

External curators publish metadata; mirrors transport untrusted bytes. Inside the proposed household boundary, an authorized parent chooses subscriptions, profiles and exceptions. Imported assertions never gain administrative authority. A supported cooperative adapter requests an authenticated plan and plays through Jellyfin. Other authorized clients, downloads and accessible file shares remain visible original-media bypasses.

Only metadata crosses the community boundary. The local service needs no writable media mount. The plugin runs inside Jellyfin's privileged process and should remain thin. A mirror authenticates neither curation correctness nor parental permission.

**PROPOSED playback contract:** one exact server/plugin/client tuple, selected source, required actions and media modes. Start with a native experiment; if it fails the first promise, consider one purpose-built cooperative adapter or narrow to metadata/marking. A dedicated adapter is a maintenance commitment and proposed scope change, not permission to fork Jellyfin. Do not silently relabel Unknown as Intro/Commercial to borrow unrelated settings.

A maintained adapter must clear transition state and correlate asynchronous results to session/source generation; stale responses cannot silently replace current plans. Native manager lifecycle behavior remains an experiment, not an established defect.

## Metadata, identity and revision-sensitive authority

**PROPOSED conceptual boundaries in one database:** Work; Edition/Timeline; Asset; validated Asset-to-Timeline binding; versioned Annotation; authority-specific CurationDecision; private HouseholdPolicy. External title IDs are provenance-bearing aliases, not infallible global identity.

A timeline declares cut, duration, timestamp origin and relevant audio language/track. OSHash locates candidates; equal duration or sampled ends cannot establish equivalent cuts. A full digest identifies bytes, not correspondence between different encodes. Namespace `jf:` fallbacks locally and bind the actual selected media source. Begin with explicitly verified bindings. Offsets require distributed matching anchors; drift requires tested rate mapping; edits require bounded piecewise mappings. Never extrapolate across unverified cuts.

Annotations need origin-qualified stable IDs, increasing origin revisions, content digest, integer-millisecond half-open spans `[start,end)`, taxonomy version, provenance, author claim/authentication state, generation method, rights evidence and publication state. Require `0 <= start < end <= verified duration`, safe cross-language numeric bounds and checked tick conversion. Unknown duration is unresolved. Unknown descriptive fields may be extensible; unknown safety-critical semantics must not be applied.

**Revision-sensitive curation:** acceptance binds origin, annotation ID, reviewed content revision/digest and timeline-binding revision. Mutation preserves the historical decision but explicitly re-evaluates applicability; ID continuity must not transfer approval to new times or bindings. Deletion/reappearance does not silently revive approval. Whether an independently curated local copy survives publisher removal is an owner choice; retain provenance and show its separate source/staleness status.

Pending/automated proposals must be excluded from curated playback until accepted by the chosen authority. This gate is not currently implemented. Do not fabricate legacy authorship, review or CC0 assent. Votes inform review but cannot overturn household-approved data automatically. Locally issued pseudonymous credentials authenticate possession/continuity without public accounts; they do not prove unique humans or defeat Sybils. Public intake is opt-in; a published port does not by itself prove Internet reachability.

A playback plan binds authenticated household principal/profile, Jellyfin item and selected source, timeline-binding revision, policy/metadata/trust revisions, capabilities, coverage and expiry. Profile/source changes invalidate it. Device capabilities are negotiation, not trusted attestation. Cache keys and asynchronous generation checks must preserve these bindings.

Proposed precedence: authorized scoped exception, explicit household rule, ordered subscribed-curator decisions, optionally enabled suggestions. Preserve disagreements and explain the chosen rule. Within applicable overlapping spans, propose skip over mute over mark; mark never cancels filtering. Unsupported actions require explicit authorized substitution or refusal, not silent mute-to-skip. Restore pre-existing mute state correctly.

Exceptions need parent authorization, profile/edition/category/action scope, expiry, revocation and private audit. Contribution credentials, LAN location, CORS or typed profile names grant no parental authority. Exceptions cannot override Jellyfin item ACLs. Restore/import must not silently revive revoked or expired permission.

## Honest failure and recovery states

**PROPOSED options; owner chooses defaults.** Fail-closed means refusal/pause by the supported player, not denial across every other route. Fail-open means authorized explicit unfiltered playback with persistent warning.

- Valid-empty means no matching annotations, not verified-safe content; coverage is explicit.
- Wrong/uncertain timeline or unsupported action means no guessed boundaries: refuse filtering or obtain authorized unfiltered approval.
- Upstream unavailable preserves the last accepted local dataset, usable only within chosen visible-staleness rules.
- Known revocation, bad signature and conflicting accepted revision are not merely stale data; stop new acceptance and apply the declared policy to cached data/active plans.
- Native refresh must distinguish unavailable from valid-empty and test ordinary/forced/cancelled/partial replacement behavior. Returning ExistingSegments or propagating failure may preserve ordinary rows under specific conditions; neither restores force-deleted rows.
- Corruption/full disk/migration failure stops unsafe writes without false acknowledgement; preserve evidence and use tested recovery.
- Cast receivers, downloads, alternate clients and file shares are unsupported unless the exact path participates. Original-media access defeats household-wide claims.

Preflight distinguishes active filtering, absent metadata, uncertain edition, unsupported capability, stale plan, recovery-unverified and explicitly unfiltered state. Successful execution cannot prove complete annotation coverage or error-free curation.

## Local deployment and a usable recovery point

**PROPOSED:** one prebuilt Go artifact embedding static UI, local SQLite on a supported local filesystem, private binding by default, no cloud account and optional existing TLS proxy. Add bounded doctor/backup/restore/upgrade surfaces, logs, deadlines, admission limits, idempotency and durable outbox. An outbox table needs no broker or CRDT.

Current Compose deploys only API and publishes without explicit loopback bind: `docker-compose.yml:7-25`. Manifest checksum is a placeholder: `plugin/manifest.json:11-15`. Compiled target is net9.0/Jellyfin.Controller 10.11.11: `plugin/Jellyfin.Plugin.Cleanyfin.csproj:4-16`. Complete release installation and other ABI compatibility remain UNVERIFIED.

Use Online Backup API, VACUUM INTO, or verified quiescent checkpoint/close [S5,S12]. Hot main-file copying and sequential changing DB/WAL copies do not establish consistency. Never delete WAL as repair. Incremental backup may restart/fail to finish under writes; interrupted VACUUM INTO may leave incomplete output. A plausible destination file is not success.

**Proposed completion contract:** write a temporary destination; check successful completion/errors; verify selected-engine/filesystem durability and durable publication; bound duration and expose failure/overdue status. Coordinate external keys/configuration with database state. Backup private policy, votes, outbox, audit, trust history and required recovery material; visible dump is not backup. Encrypt off-device copies and verify fresh isolated application restore, relationships, counts and representative reads before treating the mechanism/recovery point as usable. RPO measures age of the last completed usable off-device point, not scheduler frequency.

Driver v1.34.1 is pinned at `server/go.mod:8`; documentation lists SQLite 3.46.0 [S4]. WAL-reset advisory fixes include 3.51.3/3.50.7/3.44.6 [S6]. The race requires multiple connections to the same file and narrowly timed write/checkpoint/reset overlap. One current Store connection alone, a distinct backup destination or read-only connection alone does not establish it. Another Store/process/maintenance actor may change prerequisites. **PROPOSED gate:** verify runtime engine, pragmas and all same-file actors, then test a fixed driver/dependency combination before increasing concurrency. No current corruption, emergency or reproduction is claimed.

Separate database/API/taxonomy/plan/export versions; use ordered transactional migrations and bounded backfills; refuse unsupported newer schemas. Stage upgrades with free-space/backup/tuple checks. Binary rollback needs compatible schema; otherwise restore its matching backup and disclose later-write loss.

**Post-restore authority:** an old consistent backup can predate key/exception revocation, forget accepted high-water marks or replay an already remotely accepted outbox item. Start recovery-unverified. Reconcile current trust/authorization against trusted current information or explicit operator recovery before displaying them as current. Otherwise use only the owner's declared restricted/stale behavior; do not invent missing security history. Declare clock assumptions and handling of backward/uncertain time. Preserve submission idempotency across restore and lost acknowledgements.

## Scale without speculative infrastructure

**Assumptions, not forecasts or supported capacity:** 20 annotations/release, two votes/annotation, 1,000 stored bytes/annotation plus 250/vote, and 400 serialized bytes/annotation. Estimated DB is `N × 1,500`; raw export `N × 400`, excluding future entities, WAL, staging and backups.

| Scenario | Releases / rows | Estimated DB / raw snapshot | Assumed peak reads/writes per second |
|---|---:|---:|---:|
| Household | 1,000 / 20,000 | 30 MB / 8 MB | 2 / 1 |
| Community | 10,000 / 200,000 | 300 MB / 80 MB | 20 / 5 |
| Curator node | 100,000 / 2 million | 3 GB / 800 MB | 100 / 10 |
| Public catalog | 1 million / 20 million | 30 GB / 8 GB | 1,000 / 50 |

At an unmeasured 0.25 compression ratio, public snapshot is 2 GB; 100 daily consumers require 200 GB/day before retries. Measure compression, stored copies, staging, WAL, recovery workspace, egress and operator/moderation time. Open-source is not zero-cost hosting.

Build bounded immutable exports once and serve stored bytes; use revision-bound pagination. Measure plans, connection waits, writer occupancy, WAL, RSS, disk headroom and operator time. Fix query/export contention first; a reader pool follows the engine gate and comparative tests.

Escalations are **independent measured branches**, not a mandatory ladder: excessive snapshot bandwidth/storage windows may justify deltas; repeatable database bottlenecks may independently justify Postgres; missed rehearsed recovery objectives may justify replicas only with fencing/operator/drill ownership. No row-count threshold establishes SQLite capacity or migration necessity.

## Federation from portable snapshots

**PROPOSED sequence:** local import/export, manually trusted complete curator snapshots, authenticated mirror distribution, optional deltas when measured, independent origin-owned proposals only if communities need partitioned authorship. No concurrent overwrites of shared rows are required now.

Manifest declares format version, publisher, epoch/sequence, complete namespace scope, creation/expiry, license, counts, exact lengths and payload digests. Bound download/decompression, stage and validate before replacing only that publisher's public scope. Absence deletes imported assertions only in an authorized authenticated complete same-scope snapshot, never a partial response. Signature authenticates the publisher's completeness assertion, not actual completeness or good curation. Household overlays survive but their applicability is revision-sensitive. Unpublishing does not erase historical public copies.

Before automatic consumption, freeze key-to-publisher/scope authorization and precise signature/manifest encoding; unspecified encoding is an open gate, not an implementation detail to guess. Sign interpretation-critical metadata and exact payload digests; bootstrap trust out of band. A mirror cannot nominate its own replacement key. Persist accepted epoch/sequence/digest and quarantine rollback, forks and conflicting same-version content.

Activate dataset and acceptance history together, preferably in one transaction; trust-configuration changes and cache/plan revisions must describe the activated view. Crashes must yield old or new complete acceptance, not mismatched data/high-water marks.

**Proposed recovery contract:** compromised-key recovery is explicit operator action pinning authorized replacement key/scope and a documented fresh epoch/reset, preserving old history/audit. Ordinary updates/rotation do not discard rollback protection. Test fast-forward sequence poisoning, cross-scope claims and interrupted activation; do not accept a reset merely because a new signature verifies. Clock uncertainty/backward time and expiration need declared treatment. Offline nodes cannot learn immediate revocations. Maintained TUF tooling may be evaluated later if unattended lifecycle warrants its burden [S9]; this small protocol is not TUF-equivalent.

Deltas need base snapshot/hash, contiguous sequences, origin revisions, deduplication and tombstones; gaps require rebootstrap. Retain tombstones until old consumers must rebootstrap. Add deltas only when complete snapshots miss measured supported transfer/storage windows.

## Privacy, alternatives and owner values

Keep paths, Jellyfin IDs, household rules/exceptions and viewing events private. Prefer scheduled local snapshots over per-play upstream queries. Prefix buckets guarantee no anonymity set; exact fingerprints and contribution histories are linkable. Use scoped pseudonyms/minimal private abuse telemetry; public exports exclude household state.

**REJECTED:** metadata proxy as mandatory enforcement; hot copying as backup; votes as identity/parental authority; runtime buckets as timeline proof; generic Kodi/mpv EDL equivalence. **DEFERRED:** CRDTs, distributed databases, compulsory Git, brokers, Kubernetes, full client forks, automatic cross-cut alignment and transforms. Session companions remain timing experiments. mpv v0.40.0 has its own EDL specification [S10]; player-specific adapters remain reversible and separately tested.

Metadata-only distribution remains the boundary. Local transforms need separate technical/operating/legal evaluation, not a claim that all local transforms are forbidden or lawful. R15 licenses do not establish contributor rights, patent freedom, trademarks or jurisdiction-specific clearance. Do not relicense incompatible corpora [S11].

Owner has settled product sequencing only. First tuple/actions and exposure/timing tolerance, failure/stale/unfiltered/revocation policy, trust/privacy, legacy publication and provenance, curator/exception precedence, revision-sensitive local-copy behavior, recovery/trust-reset process, sustainable maintenance budget and longer-term enforcement scope/design remain open. No timing/SLO targets, public launch or broader architecture are approved; actual playback correctness remains experiment-gated.

## Source register and limits

Locally retained recovery verification records: original complete/blocked/blocked statuses concern report delivery, not absent research. Packet SHA256 `346cba764d45f3a60316a0c12dbab83208571a39678b676837b1ad7973bf8db2`. Study agreement is not independent evidence. The critic completed independent source review; all experiments remain open.

Sources accessed by the supplied studies/synthesis/review on 2026-10-08; alignment writer did not newly fetch them. Jellyfin/Web is pinned v10.11.11. TUF URL is live, displayed version 1.0.36, modified 2026-08-05. Other live documentation does not establish selected-runtime behavior.

- S1: https://raw.githubusercontent.com/jellyfin/jellyfin-web/v10.11.11/src/apps/stable/features/playback/utils/mediaSegmentSettings.ts
- S2: https://raw.githubusercontent.com/jellyfin/jellyfin-web/v10.11.11/src/apps/stable/features/playback/utils/mediaSegmentManager.ts
- S3: https://raw.githubusercontent.com/jellyfin/jellyfin/v10.11.11/Jellyfin.Server.Implementations/MediaSegments/MediaSegmentManager.cs
- S4: https://pkg.go.dev/modernc.org/sqlite@v1.34.1
- S5: https://www.sqlite.org/backup.html
- S6: https://sqlite.org/wal.html#wal_reset_bug — page updated 2026-08-25.
- S7: https://raw.githubusercontent.com/jellyfin/jellyfin/v10.11.11/Jellyfin.Api/Controllers/SessionController.cs
- S8: https://raw.githubusercontent.com/jellyfin/jellyfin/v10.11.11/MediaBrowser.Model/Session/GeneralCommandType.cs
- S9: https://theupdateframework.github.io/specification/latest/ — displayed 1.0.36; not a pinned URL.
- S10: https://raw.githubusercontent.com/mpv-player/mpv/v0.40.0/DOCS/edl-mpv.rst
- S11: https://sponsor.ajay.app/database — recovered unversioned terms; no new legal review.
- S12: https://www.sqlite.org/lang_vacuum.html — live documentation, selected-engine durability still requires verification.

No live compatibility, installation, load, restore or legal test occurred. Earlier synthetic counterexamples are not production measurements; recovered Go tests could not run. Rendering/deployed links also remain unverified.
