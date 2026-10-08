# Independent architecture review

## Owner verdict

**Proceed with the proposed small, household-local direction as a development hypothesis—not as a production-safety or scalability verdict.** One Go/SQLite metadata-and-policy service, one deliberately supported cooperative adapter, and curator-owned snapshots avoid unnecessary infrastructure. The difficult work remains playback correctness, trustworthy publication and recovery, not selecting a distributed database.

The consequential owner choice is still **cooperative filtering versus deliberate-bypass resistance**. The latter requires control of original-media access paths and is not supplied by a metadata proxy. No choice was approved by approving this workflow.

**Production-promise blockers are not experimental blockers.** Current Unknown delivery and refresh degradation prevent dependable-protection claims. They do not prevent separately authorized experiments on isolated synthetic fixtures. No new experiments ran here.

## Evidence status

Baseline: `2426cef1ab345a5a829b2ce7a6fe8fab8661011e`; access date **2026-10-08**. The recovered packet in locally retained verification records matched SHA256 `346cba764d45f3a60316a0c12dbab83208571a39678b676837b1ad7973bf8db2` before use. Its original complete/blocked/blocked statuses describe delivery limitations; all three studies contain substantive research. Agreement was not counted as independent evidence.

**IMPLEMENTED** below means inspected code. **SOURCE-VERIFIED** means the cited upstream source supports the bounded claim. **PROPOSED** contracts remain unimplemented. Runtime compatibility, exposure timing, restore success, throughput and legal clearance remain **UNVERIFIED**. Proxy-as-enforcement, unsafe hot copying and guaranteed prefix anonymity remain **REJECTED**.

## Playback: the main warning survives scrutiny

**R1 — BLOCKER to production protection claims; supported.** `plugin/CleanyfinSegmentProvider.cs:67-91` emits Unknown and discards actions/categories. Pinned Web defaults Unknown to None [S1], requests enabled types only [S2:84-116], and implements skip/prompt rather than arbitrary mute [S2:70-81]. Short automatic skips are suppressed below one second when StartTicks is nonzero; prompts have analogous nonzero start/end guards below three seconds. Previous-position logic can suppress replayed skips.

The proposal is right to avoid universal client claims. It must not overcorrect into saying configured Unknown can never work. E1/E4 should include default/configured settings, zero-start controls, seek-back, resume, measured audio/video output and all advertised media modes. A passed sample is evidence for that tuple, not universal no-exposure assurance.

**R2 — BLOCKER to an outage-preservation promise; conditional source finding.** `plugin/CleanyfinSegmentProvider.cs:61-91` catches API failures into empty output. Upstream ordinary refresh deletes changed provider output [S3:106-138]. The deletion chain requires an enabled supporting provider actually to run, pre-existing provider rows, and a usable parent cancellation token/database operation. A provider-local timeout can meet these conditions; a cancelled parent may prevent deletion. An outage without refresh does not itself prove deletion.

Force-overwrite pre-deletes all item segments [S3:76-79]. Returning ExistingSegments or throwing cannot recover rows already deleted. Replacement is also not shown atomic: per-segment inserts follow deletion [S3:143-166]. Expand E2 to parent cancellation, disabled/no-refresh controls and failure partway through replacement. Distinguish lost Jellyfin materialization from deletion of canonical API annotations.

**R3 — MATERIAL experimental gap.** Web's manager stores asynchronous results without source/request correlation [S2:25-35]; playback start resets counters but does not clear its segment array [S2:84-117]. If the same instance spans items, a late A response can overwrite B, or retained A intervals can be consumed while B loads. Lifecycle reuse and a live malfunction were not verified. Add delayed/reordered A/B fetches and profile changes to E1/E5. A maintained adapter needs transition clearing and session/source generation correlation. Do not publish this hypothesis as a reproduced defect.

## Publication and matching: explicit authority must survive change

**R4 — MATERIAL; supported.** Pending rows are eligible for exact/prefix/dump reads: `server/internal/store/store.go:139-161,181-184,205-222`. Application routes lack write authentication, and identity is caller-supplied: `server/internal/api/api.go:35-47,191-249`. Two claimed identities can drive a visible row below the voting threshold; vote replacement does not prove distinct humans.

The proposed quarantine is necessary but not implemented. E8 must verify every exported/read/playback view, not merely queue placement. Authentication of contribution continuity must remain separate from parental administration. Existing controller authentication does not establish per-item authorization: `plugin/FingerprintController.cs:15-43` and `plugin/SegmentsController.cs:20-55` warrant E5, not an asserted exploit.

**R5 — MATERIAL missing revision contract.** The matching correction is sound. `plugin/Moviehash.cs:22-66` samples ends and size; provider lookup and SQL do not verify duration or selected-source equivalence. Equal duration, sampled hash or full byte digest cannot independently establish cross-encode timeline correspondence.

However, preserving household overlays while increasing annotation revisions is insufficient. Approval of ID X must not silently approve X's newly changed boundaries or timeline. Bind curation to origin, ID, content revision/digest and binding revision. Preserve the historical decision but explicitly re-evaluate applicability after mutation. Define locally retained copies after upstream deletion. Add same-ID mutation, deletion/reappearance and changed-binding fixtures to E3/E7.

## Federation: signatures do not complete the trust model

**R6 — MATERIAL contract completion needed.** Origin-owned complete snapshots, scoped absence-as-deletion, bounded staging and conflict quarantine are appropriate. No importer currently implements them; the inspected schema is only segments and votes: `server/internal/store/store.go:41-65`.

Require explicit key-to-publisher/scope binding and signature encoding. Commit active dataset and accepted epoch/sequence/digest consistently, preferably in the same database transaction. A crash between those states must not permit rollback or permanently reject the dataset it failed to activate. Cache/plan revisions must describe the activated view.

Define operator-authorized epoch/key recovery, including a compromised signer that advances sequence numbers. Test cross-scope manifests, fast-forward poisoning, interrupted activation and clock rollback. TUF 1.0.36 provides relevant rollback/freeze and recovery concepts [S8], not evidence that this custom protocol inherits them. TUF need not become a first-release dependency.

A valid signature authenticates an authorized publisher's assertion of completeness—not good curation or actual completeness. Offline clients cannot learn immediate revocation. Local policy must state what happens to already accepted data and active plans after a revocation is learned.

## Recovery: consistent bytes are necessary, not sufficient

**R7 — MATERIAL post-restore authority gap.** An old backup may forget a key/exception revocation or a newer accepted sequence. Restoring it cannot prove current trust history. Make recovery-unverified an explicit state: reconcile against trusted current information or operator recovery before describing restored authority as current; otherwise apply the owner's restricted/stale policy. Expiry requires a clock assumption and an uncertain/backward-clock rule.

E10 should restore a backup deliberately predating revocation. E11 should cover a remotely committed submission whose acknowledgement was lost and an old restored outbox, not only retries after acknowledged success. Preserve idempotency across recovery.

**R8 — MATERIAL qualification; the advisory is real but conditional.** `server/go.mod:8` pins modernc.org/sqlite v1.34.1; its documentation reports SQLite 3.46.0 [S4]. The official WAL-reset advisory includes that version and fixes in 3.51.3/3.50.7/3.44.6 [S5]. It requires multiple same-file connections and narrowly timed checkpoint/reset/write overlap.

`server/internal/store/store.go:69-75` limits each Store handle to one connection. That alone does not satisfy the race. Another process/handle/maintenance actor may alter the situation; a separate backup destination or a read-only connection is not itself proof. Retain a conservative tested fixed-engine gate before adding actors, but do not claim current corruption or reproduction. Verify runtime engine and all same-file actors.

**R9 — MATERIAL acceptance refinement.** Online Backup API, VACUUM INTO and verified quiescence are valid candidates [S6–S7]. They are not blanket success guarantees. Incremental backup can restart under writes and fail to finish; interrupted VACUUM INTO may leave incomplete output. Define temporary destination, successful completion, durability behavior for the selected engine, durable publication, bounded duration and fresh application restore. Coordinate external keys/configuration with database state.

Measure RPO from the last completed usable off-device recovery point, not scheduler frequency. Never discard WAL or replace this procedure with sequential hot copying. Public dump omits recovery state. Unsafe comments still exist at `docker-compose.yml:5` and `server/internal/store/store.go:3`; source-comment cleanup outside the documentation scope should be explicitly deferred.

## Feasibility, cost and reversible growth

**R10 — MINOR roadmap ambiguity.** The arithmetic is internally consistent: 20 million assumed rows imply 30 GB database, 8 GB raw snapshot, and—at an unmeasured 0.25 ratio—2 GB compressed; 100 daily consumers imply 200 GB/day. These are scenarios, not forecasts or capacity results. Add staging, WAL, recovery copies, retries, moderation and operator time when measuring costs.

Make escalation branches independent. Measured snapshot-transfer costs may justify deltas; measured database contention may independently justify database changes. The roadmap should not require deltas before investigating an unrelated writer bottleneck. E9 needs fixed hardware, workload mix, concurrency, offered/completed load, export/import overlap, resource caps and error denominators. No row count establishes SQLite failure or success.

Keep conceptual entities inside one application and implement only boundaries needed by the first experiment. The Go service, Jellyfin plugin and client integration still create multiple maintained compatibility surfaces. A dedicated adapter is a meaningful maintenance commitment, not a cheap label change. Neither full federation nor unattended key governance should delay learning whether the first playback promise is achievable.

**R11 — MINOR boundary preservation.** Prefix queries disclose complete matching fingerprints and paths are logged: `server/internal/api/api.go:71-75,128-160`. The proposal correctly rejects guaranteed anonymity. Metadata-only distribution and repository licenses do not establish import rights or transformation legality. Legal sources were not re-fetched here; no new clearance conclusion is warranted.

## Required owner choices and review limits

Owner choices remain cooperative versus bypass-resistant scope; first client/actions and exposure tolerance; stale/unfiltered authorization and active-session revocation; legacy publication and revision-sensitive local curation; recovery objectives and sustainable operating budget. Nothing here resolves them by implication.

Nine selected repository files and eight successful primary fetches were inspected; no searches, builds, tests, experiments or repository writes occurred. Initial/final status retained only the pre-existing `cleanyfin.portagenty.toml` modification. Existing synthetic checks and the recovered unavailable Go toolchain are not new passing tests. This independent review is complete; implementation and feasibility validation are not.

## Primary sources

All accessed 2026-10-08; Jellyfin/Web pinned to v10.11.11.

- S1: https://raw.githubusercontent.com/jellyfin/jellyfin-web/v10.11.11/src/apps/stable/features/playback/utils/mediaSegmentSettings.ts
- S2: https://raw.githubusercontent.com/jellyfin/jellyfin-web/v10.11.11/src/apps/stable/features/playback/utils/mediaSegmentManager.ts
- S3: https://raw.githubusercontent.com/jellyfin/jellyfin/v10.11.11/Jellyfin.Server.Implementations/MediaSegments/MediaSegmentManager.cs
- S4: https://pkg.go.dev/modernc.org/sqlite@v1.34.1
- S5: https://sqlite.org/wal.html#wal_reset_bug — page updated 2026-08-25.
- S6: https://www.sqlite.org/backup.html — live documentation.
- S7: https://www.sqlite.org/lang_vacuum.html — live documentation; selected-engine behavior still requires verification.
- S8: https://theupdateframework.github.io/specification/latest/ — displayed version 1.0.36, modified 2026-08-05; the URL itself is not version-pinned.
