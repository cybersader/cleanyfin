# Prove the household product before expanding it

Research access: **2026-10-08**. **Owner-approved product sequencing; remaining roadmap details and experiments PROPOSED, none executed in this continuation.** Companion [architecture](04-architecture-proposal.md) defines claim labels and sources; [independent review](06-independent-review.md) supplies corrections. No stage grants implementation, public-intake or experiment authority.

## Decision and strategy

**Owner sequencing decision, 2026-10-08:** dependable filtering on explicitly supported cooperative clients is the first-release target. Server-side enforcement/deliberate-bypass resistance stays on the LONGER-TERM roadmap, not abandoned and not a first-release prerequisite. Owner response: “Yep but keep server-side enforcement on the longer-term roadmap”. This chooses sequencing, not architecture, technique, first client/actions, failure policy, exposure tolerance, timing/SLO targets, public launch or legal clearance. Actual playback correctness remains UNVERIFIED and experiment-gated.

**Remaining proposed strategy:** establish one honest recoverable cooperative filtering experience. Expand publication/distribution only after identity, policy and failure behavior are demonstrable. Do not spend the first maintenance budget on federation while playback correctness is unresolved.

A failed native experiment can leave canonical metadata intact and motivate a different adapter or metadata-only scope. It cannot be redefined as successful protection. R1/R2 block production promises, not separately authorized isolated tests.

## Staged evidence gates

| Stage | Demonstrable exit and blockers | Rollback, non-goals and maintenance |
|---|---|---|
| 0. Correct the contract | Review recovered evidence/critic; cooperative first-release/enforcement longer-term sequencing is owner-approved. First action/client, exposure tolerance and failure policy remain open. Corrections remain factual or explicitly proposed. | Preserve history/R15. No new infrastructure. Retain separately scoped longer-term enforcement track below; no technique approved. Maintain a small evidence/decision register. |
| 1. Prove one playback path | E1–E5 demonstrate exact tuple/source, output/timing, profile isolation and declared refresh behavior. Native failure selects another cooperative-adapter proposal or narrower scope. | No fleet/cast/download assumptions or automatic cut alignment. Disable adapter on unexplained exposure; retain metadata. Maintain fixtures per advertised tuple. |
| 2. Make a household recoverable | E6/E10/E11 demonstrate release installation, completed usable backup, restore-based rollback, acknowledgement/idempotency and post-restore authority. Verify tested fixed engine before new same-file concurrency. | No public operation or uptime guarantee. Rollback uses compatible artifact/data pair with disclosed later-write loss. Maintain packaging, migration fixtures and restore drills. |
| 3. Share without surrendering authority | E7/E8/E12 demonstrate scoped snapshots, atomic acceptance, revision-bound local decisions, quarantine/provenance and offline privacy. Resolve legacy publication/rights. | No shared-row concurrent writes or compulsory central account. Suspend feeds/intake without losing local policy. Maintain moderation, key recovery and compatibility fixtures. |
| 4. Grow against measured limits | E9 measures declared workloads/resources. Owner accepts budgets/SLO/recovery targets and operator time. Branch by actual bottleneck. | Bandwidth may motivate static distribution/deltas; database contention may independently motivate DB changes. Neither branch is a prerequisite for the other. Require rollback and named operator for every escalation. |

Failure of a safety-critical gate blocks its advertised feature, not all metadata development. Implementation convenience cannot choose unresolved owner values.

## Acceptance discipline

Every experiment records commit/artifact digests, server/plugin/client/engine versions, OS/filesystem/hardware, fixture hashes, settings, expected transitions, raw observations, result, budget and cleanup. Separate source inference, synthetic counterexamples, process-crash tests and actual playback measurement.

Predeclare timing/exposure tolerance, freshness/clock assumptions, resource limits and data-loss tolerance. Candidate options—not commitments—are household RPO 24 hours/RTO 2 hours or community 1 hour/1 hour; p95 reads below 200 ms, p99 below 1 second and unexpected errors below 0.1% on declared hardware. RPO is age of last completed usable off-device recovery point, not backup schedule. Owner may choose different values before results, not after failures.

All fixtures use synthetic or authorized media/disposable data. No experiment below reports a success or authorizes services, installs or production access.

## Twelve falsifiable experiments

### E1 — Does native Unknown support the first promise?

Use isolated Jellyfin/Web v10.11.11, two users, Unknown plus Intro/Commercial controls, durations 0.2/0.7/1.5/4 seconds, default/configured actions, zero-start and nonzero-start/end controls, seek-back and resume. Record requested types, HasSegments/source IDs, settings and measured output—not just commands.

Add delayed-response lifecycle cases: delay A; start B with different spans; complete B then A; inspect requests, stored/evaluated spans and rendered output. Test failed fetches and source/profile transitions. Same-manager reuse is a conditional hypothesis, not assumed runtime fact.

**Pass:** exactly advertised behavior occurs on documented tuple/source/settings. **Kill:** omitted/suppressed required actions or stale/wrong-source action. **Rollback/cost:** remove metadata fixtures/restore settings; retain matrix per supported tuple. A maintained adapter must clear transition state and correlate generation/source results. Basis: architecture S1–S2/provider `:48-92`.

### E2 — Can refresh failure preserve an honest state?

Preseed materialized rows. Inject provider-local timeout, HTTP error, malformed response, legitimate empty and explicit revocation. Exercise ordinary/force-overwrite refresh, usable versus cancelled parent token, disabled/non-supporting/no-refresh controls and failure during later replacement inserts. Inspect materialization separately from canonical API annotations and supported-player status.

**Pass:** predeclared preservation/deletion rules hold; revocation is not concealed by stale cache; missing protection never appears active; partial replacement is detected. **Kill:** silent clearing, partial state or concealed stale protection. **Rollback/cost:** restore isolated Jellyfin/data snapshot; maintain failure fixtures. ExistingSegments/exception approaches may preserve ordinary rows under specific conditions, not force-deleted rows. Basis: S3/provider `:61-91`; no current preservation implementation.

### E3 — Is the selected asset bound to the reviewed timeline?

Use remuxes, equal-duration different cuts, same-sampled-end candidates, offsets/rate changes, inserted scenes, multiple selected sources/audio tracks. Mutate an approved annotation's times/binding under the same ID; delete/reintroduce it.

**Pass:** no unverified mapping is automatically accepted; accepted boundaries match distributed anchors within predeclared tolerance; source changes invalidate plans; old approval does not silently transfer to new content/binding. **Kill:** hash/duration/ID alone accepts a wrong cut or mutated approval. **Rollback/cost:** remove experimental bindings; maintain small licensed fixtures. Synthetic OSHash counterexamples are not accuracy measurements. Local-copy survival rules remain owner-controlled.

### E4 — Can one cooperative adapter execute its plan?

Measure synthetic audio/video markers under direct play/direct stream/transcode. Test skip/mute/mark, overlaps, resume, seek into spans, speed changes, manual volume and already-muted state. Capture actual prohibited/allowed output around boundaries.

**Pass:** measured output satisfies chosen tolerance and restores user state; unsupported actions explicitly refuse/substitute only with authorization. **Kill:** silent substitution or output outside the promise. **Rollback/cost:** disable adapter; retain scheduler/regression fixtures. No-exposure requires no prohibited output in measured cases, not eventual seek or dispatch acknowledgement. No untested fleet inference.

### E5 — Are profiles/exceptions and asynchronous plans authorized?

Use two child profiles/one parent. Attempt wrong-profile selection, unauthorized item lookup, stale/cross-session plans, expired/revoked exceptions and changed sources. Delay/reorder A/B/profile-plan responses and failed fetches. Probe direct server/download/file-share access and one cast receiver only within separate authorized scope.

**Pass:** forbidden plan/exception requests are denied; scope/revision/generation changes invalidate old authority; stale responses cannot replace current plans. **Kill:** unauthorized authority; original-media bypass defeats any household-wide claim. **Rollback/cost:** revoke fixtures/restore isolated settings; retain authorization/lifecycle matrix. Controller source raises questions, not a proven exploit.

### E6 — Can another person install, upgrade and undo it?

On a clean supported host use actual release artifacts, not a checkout: install API/UI/plugin, connect one declared player, submit mark and observe advertised result. Upgrade through migration, interrupt and restore matching artifacts/data.

**Pass:** documentation suffices, checksums/tuple compatibility are real, data/authority survive as declared and rollback loss is explicit. **Kill:** placeholder artifact, undeclared service, manual DB repair or unsupported current-authority display. **Rollback/cost:** pre-upgrade usable snapshot/matched binaries; one golden path before extra platforms. Measure time, not an assumed five minutes.

### E7 — Does snapshot acceptance preserve ownership and reviewed revisions?

Import complete A then B with removals/revisions. Add duplicates, partial/truncated/oversize payloads, replay, conflicting same-version content, cross-scope claims and key/epoch recovery. Test same annotation ID with changed span/binding, deletion/reappearance and independently retained local copy. Poison sequence forward with compromised signer; exercise expiry/backward/uncertain clocks. Crash before/after dataset and acceptance-history activation. Later deltas add base/gap/tombstone cases.

**Pass:** only old/new complete authorized publisher scope with matching epoch/sequence/digest is active; local decisions survive as history but applicability is explicitly re-evaluated; caches/plans correspond to activation; unauthorized reset/deletion/replay fails. **Kill:** mismatched data/history, mixed namespace, transferred approval or automatic trust reset. **Rollback/cost:** retain old import/trust history; bounded fixtures and operator recovery procedure. Signature encoding/key-to-scope binding must be frozen before test acceptance. A signature's completeness assertion is not proof of correct comprehensive annotations.

### E8 — Does account-free contribution resist cheap abuse without inventing authority?

Use many fresh credentials, impersonated handles, duplicate votes, conflicting curators and automated proposals. Inspect exact reads, prefix reads, dumps, curated plan output and review queue; exercise legacy transition explicitly.

**Pass:** unreviewed input is excluded from every claimed curated view; credential continuity is authenticated; local authority survives hostile totals; review queues are bounded and no copied data acquires fabricated consent. **Kill:** unauthenticated votes remove household protection or pending is advertised as quarantine. **Rollback/cost:** pause public intake; retain local reads; measure reviewer minutes/backlog. Key possession does not prove humanity; reachability does not prove public Internet exposure.

### E9 — What workload can the simplest node sustain?

Start household-sized and progress within approved resource cap. Declare hardware/engine/filesystem, corpus distribution, query/write mix, concurrency, uniform/hot-key skew, cold/warm cache, offered versus completed load, export/import overlap, duration and latency/error denominators. A bounded 30-minute peak period is a candidate specification, not an executed benchmark. Record plans/waits/writer occupancy, RSS, WAL, staging/backup storage, egress and operator time.

**Pass:** predeclared objectives with bounded queues/files/resources. **Kill:** runaway memory/WAL, missed objectives or concealed rejected load. **Rollback/cost:** stop generator/remove corpus; repeat after relevant changes. Isolate bottleneck before proposing readers/Postgres/deltas. Bandwidth and database escalation are independent; count/arithmetic alone establishes neither capacity nor migration.

### E10 — Is a completed backup a usable recovery point?

After engine/actor gate, back up during keyed writes using selected consistent mechanism. Exercise busy/restarted/noncompleting backup and interrupted destination creation. Verify completion/error checks, selected-engine durability, temporary-to-durable publication, duration bound and overdue reporting. Coordinate keys/configuration. Restore fresh offline across retained schemas.

Deliberately restore a backup predating key/parental-exception revocation and newer accepted high-water marks. Check digest/integrity/relationships/counts/votes/policy/outbox and representative APIs. Test trusted-current reconciliation or explicit operator recovery, uncertain clocks and permitted restricted/stale state.

**Pass:** known usable contents/RPO/RTO; restored authority cannot silently appear current or revive revoked permission. **Kill:** integrity-only success, incomplete plausible file, missing private material or fabricated trust freshness. **Rollback/cost:** original untouched; recurring off-device drill. Main-file hot copy is not a candidate mechanism.

### E11 — Are crash/retry/restore failures bounded and idempotent?

Crash at commit/outbox/import/acceptance boundaries. Retry identical submissions; conflict on changed-body nonce reuse. Simulate remote commit with lost acknowledgement; restore old outbox containing already accepted items. Exhaust quota-limited disk, deny writes and stall checkpoint progress.

**Pass:** one logical record per authenticated nonce across retry/restore, no false acknowledgement, consistent active acceptance/plan state, bounded retry/repair and chosen recovery objectives. **Kill:** duplicate remote acceptance, silent loss, trust-state mismatch or manual WAL deletion. **Rollback/cost:** fresh disposable volume; maintain fault hooks. SIGKILL tests process crash, not power-loss durability.

### E12 — Does local operation avoid hidden privacy dependencies?

Inspect outgoing requests/logs/public exports. Disconnect upstream; use imported reads, owner-permitted stale/recovery states and queued submissions. Measure actual prefix buckets/dictionary linkage and private-field leakage.

**Pass:** household IDs/policy/history stay private; imported reads have no per-play upstream dependency; stale/offline/unknown state is honest; retries bounded. **Kill:** private leakage or anonymity claimed from sparse buckets. **Rollback/cost:** disable sync/remove captures securely; repeat for new fields. Offline cannot promise newly learned revocation.

## Retained longer-term track — Server-side enforcement / deliberate-bypass resistance

**Owner-retained product objective (2026-10-08), not a cooperative first-release prerequisite.** No delivery date or implementation technique is approved; this is sequencing, not abandonment.

**Entry criteria:** define an explicit threat model and authorization boundary, then scope a feasibility spike accounting for original-media access, alternate clients, downloads and file shares; assess client/format compatibility and operating cost. Later design must define the scope. No enforceability against a server administrator or someone controlling the media is claimed.

**Exit criteria before enforcement claims:** scoped spike/test evidence supports the access/authorization boundary, compatibility and operating budget; demonstrates reliable failure/recovery behavior; and includes legal review when applicable. Metadata filtering or remote commands alone are not unbypassable playback enforcement. No DRM/circumvention scope or legal clearance is granted. Keep E1–E12 unchanged; later enforcement experiments require their own scoped design/evidence, not reinterpretation of cooperative passes.

## Deferred branches and owner choices

Player-specific exports need their own pinned format/action/timeline tests before advertising Kodi/mpv. Sidecars do not require a default writable media mount. Do not add this branch merely to evade failed playback gates.

No stage approves transforms, DRM circumvention, full client fork, global accounts, multi-writer machinery or production benchmarks. Local transforms require separate scope, cost analysis and legal evaluation. R15 remains recorded without implying contribution/import rights or legal clearance.

Sequencing is settled; owner still chooses first tuple/actions/exposure tolerance; failure policy and authorized unfiltered/stale behavior, active revocation/uncertain clocks; trust/privacy, legacy publication/public provenance; revision-sensitive curator/exception/local-copy precedence; supported hardware/hosting/RPO/RTO/SLO, recovery and weekly maintenance/moderation budgets; actual longer-term enforcement scope/design. Implementation must not silently decide these.

## Knowledge-ops scaffolding

Keep one small evidence register and proposed decision records in this workstream, not a tracking service. Claims record status/source/version/access date, evidence path/hash, owner, counterevidence and next gate. Experiments record objective/fixed inputs/oracle/budget/result/raw failures/cleanup. Compatibility rows name exact tuple/actions/media modes/failure behavior and last actual test.

Use E1–E12 stable IDs; decision records remain proposed until explicit owner acceptance. Preserve R13 reasoning/history with dated factual supersession rather than retroactive approval. Synchronize accepted corrections into orientation/mirrors only after exact preimage checks; preserve frontmatter and historical build results. No blanket regex or research-to-completed-milestone conversion.

Independent review is complete; all experiments remain open. Recovered synthetic checks and unavailable Go toolchain retain their limits. Alignment only validated prospective replacement strings/source links, not application, deployed links, builds or feasibility.
