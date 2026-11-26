# Playback delivery contract

`Cleanyfin.Delivery` is a pure .NET 9 library for [playback plan v1](schema/playback-plan-v1.schema.json), source/filtered clock mapping, cut-only mpv EDL rendering, and exact evidence admission. It follows the [source-bound plan, measured adapter pattern](../.claude/13-DELIVERY-PATTERN.md). It does not read media or perform authorization, token issuance, HTTP requests, or player capability discovery.

Use the schema for structure and `PlanValidator` for strict wire and semantic checks. Pass independently verified source and clock facts, current authority/revision, and accepted annotation digests through `VerifiedPlanContext`. Identities in the JSON grant nothing. The caller owns freshness and revocation after parsing; the renderer needs independently verified player-clock identity and a trusted configured origin.

To add an adapter, define its accepted domain and translate the validated plan using the shared timeline. Preserve every required semantic or refuse the entire plan with a typed reason; never silently play the original. Unit tests do not establish player support. Measure and register the actual playback tuple before offering it.

## Resolver

`HouseholdResolver` takes a current, [accepted local snapshot](schema/annotation-snapshot-v1.schema.json), a [private profile policy](schema/household-policy-v1.schema.json), and the authenticated request's exact verified source, clock/watermark and validity. The caller must select the current policy and snapshot together, verify the reviewed track binding, allocate a fresh non-reused local revision for each issuance, and invalidate earlier generations when source, policy, trust or accepted inputs change. Parsing neither document proves that it was reviewed or authorized. The timeline key is separate: renewals and provenance-only changes preserve it if the source binding, track set and normalized cut boundaries still match.

Add one rule per category, e.g. `{"category":"violence","minimumSeverity":2,"filter":true}`. A filter rule selects that severity and above (0–3); `filter:false` or no rule leaves that category unselected. Only selected `skip`/`cut` becomes a cut; `mark` is descriptive, and required `mute`, unknown actions, invalid inputs or stale snapshots refuse the whole offer. There is no downgrade, partial plan or original fallback. Validity is at most 30 minutes and cannot outlive the accepted snapshot.

The snapshot's `contentSha256` is the lowercase SHA-256 of the UTF-8 text `annotation-v1|ref|category|severity|action|startMs|endMs`, substituting exact values (base-10 integers; no whitespace or newline). It includes the ref and the original action spelling. It detects content changes under a reviewed ref; it is not a signature or a substitute for local acceptance.

From the repository root with .NET SDK 9.0.x:

```sh
dotnet test tests/Delivery.Contract.Tests --filter Category=Plan
dotnet test tests/Delivery.Contract.Tests --filter Category=Mpv
dotnet test tests/Delivery.Contract.Tests --filter Category=Resolver
dotnet test tests/Delivery.Contract.Tests --filter Category=Edition
dotnet test tests/Delivery.Contract.Tests --filter Category=Formats
```

**mpv renderer, adapter revision 2.** mpv reads cut-list times as the file's own timestamps, so `MpvEdlRenderer` adds the file time of the plan's source zero (exact, from the verified track clocks; a disagreement refuses with `UnverifiedPlayerClock`). It writes one of three measured forms: `Joint` (one partition, boundaries on the instant); `JointBeforeInstant` (interior boundaries 5 µs earlier; Matroska with AC-3/E-AC-3, whose frame times mpv reads a hair early); and `SplitPictureAndSound` (picture and sound partitions, a quarter sample before and after; sound without inter-frame overlap). For a source whose zero is file time 0 rendered `Joint`, the output equals revision 1 byte for byte. `EvidenceSourceLabel` names the exact stream layout an evidence row must match. See [common formats](../knowledge-base/01-working/common-formats-2026-11-24/01-common-formats.md).

**Subtitle-bearing mpv layouts use adapter revision 3 (R19).** A cue is removed in full if its exact packet interval intersects or touches either end of a skip. Only surviving cues are mapped onto the edited clock, with inward rounding to the sidecar format. mpv's EDL declares only the original video and audio tracks; the selectable subtitle partitions fetch the safe text from separate, revocable tickets. The plugin keeps that text in memory only. Supported input is embedded MKV SubRip or ASS and plain MP4 `mov_text`, subject to the exact evidence tuple and packet/track checks. Styled `mov_text`, bitmap, external or unknown subtitles, and sources whose clocks or bounded extraction cannot be verified get no filtered offer. See the [R19 desktop-mpv receipt](../tools/playback-lab/prototypes/common-formats/receipt-r19-2026-11-25.json) for both Jellyfin lines. Wholphin and TV selection are unmeasured.

The golden pair uses the lab's timing with synthetic asset and authority identities; it is not proof of a live binding.

`Edition.cs` is how a live binding is proven: the full SHA-256 of a file plus the stream layout probed from it (the snapshot's `sha256`, `tracks`, `timelineRef` and `bindingRev`), valid only while the file's `statx` inode, size, mtime and ctime are unchanged, and hashed while checking that they do not change underneath ([design](../knowledge-base/01-working/marking-foundation-2026-11-24/02-household-file-binding.md), [edition-record-v1](schema/edition-record-v1.schema.json)). `Category=Edition` covers identity and every invalidation (replace, in-place edit with mtime kept, touch, hard link, rename, re-encode, timeline-only change, change during the hash).

## Marking

`Cleanyfin.Marking` turns rough presses into the accepted snapshot above, following the [marking foundation design](../knowledge-base/01-working/marking-foundation-2026-11-24/01-marking-foundation-design.md). Nothing downstream changes: the last step writes an ordinary `annotation-snapshot-v1`.

| Step | Document | Rule |
|---|---|---|
| Press on the phone | [capture-v1](schema/capture-v1.schema.json) | Append-only, fuzzy, never filters. Any household member may add one; a `suggester` (a household compute service) may add only `service` captures. |
| Draft and refine | [mark-v1](schema/mark-v1.schema.json) | A draft's bounds are only the capture's search window (`boundsSource: capture-window`) and cannot be accepted. A person sets exact source-clock bounds; every edit is a new `rev` in `proposed`. |
| Accept | [audit-event-v1](schema/audit-event-v1.schema.json) `mark.accept` | Reviewers only, naming `ref@rev` and the existing `contentSha256`. One person may mark and approve unless the household requires a second reviewer (R17). |
| Publish | `annotation-snapshot-v1` | Only latest revisions in `accepted` with a matching `mark.accept` in a verified log. The bytes are re-read by `ResolverInputReader` before they are returned. |
| Policy | [household-governance-v1](schema/household-governance-v1.schema.json) + viewer `household-policy-v1` | Stricter wins; a viewer can tighten but never loosen a profile's floor. The result is the existing `household-policy-v1`. |
| Labels | [taxonomy-v1](schema/taxonomy-v1.schema.json) | Household concepts roll up to one core category through at most two others. Snapshots carry only the core category. |

A capture on a filtered clock maps back to source time through the issued plan's kept ranges, or refuses (`UnknownPlan`) and stays for manual placement. The audit log is hash-chained JSON Lines: an edited, removed or reordered line is evident; dropping the newest lines is evident only against a recorded head (the plugin's store keeps one in `audit.head`), and none of this protects against whoever controls the disk. `IMarkingStore` is the seam: slice 1 has in-memory and plain-file stores; slice 2 puts the same interface behind the household plugin (R16).

In a household the Jellyfin plugin is the store, with a phone capture page and a review page on Jellyfin's own origin ([plugin/README](../plugin/README.md#household-marking-slice-2)). The lab CLI turns a phone export plus a reviewer's exact bounds into accepted marks and a snapshot. It reads no media and talks to no server:

```sh
dotnet run --project delivery/Cleanyfin.Marking.Cli -- review --store <private dir> \
  --governance governance.json --binding binding.json --captures cleanyfin-captures.json \
  --decisions decisions.json --actor jf:user:<id> --snapshot-rev home:review:10 --shelf-root <lab shelf root>
dotnet run --project delivery/Cleanyfin.Marking.Cli -- verify-audit --store <private dir>
dotnet test tests/Marking.Contract.Tests --filter Category=Marking
```

`decisions.json` is `{"decisionsVersion":1,"marks":[{"capture":"cap:…","mark":"home:mark:1","startMs":5480,"endMs":10520,"severity":2,"action":"skip"}]}`; `category` may be omitted when the capture's quick button names exactly one core category or a `subcategory` names it. `--shelf-root` writes `<root>/snapshots/<itemId>.json`. The contract tests run a real phone export through this CLI and the existing resolver to a plan byte-identical to the hand-placed golden plan.

## Evidence register

Each [row](evidence/register-v1.json) names one exact adapter revision, app version/build, backend, device/output mode, server/plugin build and source layout. It lists actions, controls and marked/kept picture and sound outcomes, with capture limits and an evidence path. `null` is an exact missing value, not a wildcard. Production requires `eligible`, all four outcomes passing and all five controls exercised. **Nothing qualifies today.** Support claims in docs come from this register, not from DirectPlay flags or successful API tests.

Run `python3 tools/playback-lab/check-plugin.py --case eligibility` to check admission, refusals and revocation in the disposable pinned lab. To add a measured row, use the [hard fixture and scorer](../tools/playback-lab/README.md#shared-hard-fixture-compare-playback-paths) on the actual tuple. Record the positive uncut control, straight cut, forward/back seeks, persisted resume and restart; score marked and kept picture and PCM, including the join; record latency and detection limits. Keep partial results experimental and link the original receipt. `dotnet test tests/Delivery.Contract.Tests --filter Category=Eligibility` checks the [closed schema](schema/evidence-register-v1.schema.json), reader and admission. The check for an existing eligible-row evidence path is a review tripwire, not a security control or proof of measurement.

**Desktop mpv is still experimental.** The [2026-11-22 receipt](../tools/playback-lab/prototypes/exact-audio/joint-receipt-2026-11-22.json) for the H.264/AAC fixture records all 374 kept pictures and 718,080/718,080 kept stereo samples in a single playback with both tracks selected. Zero samples differed from the uninterrupted decode with the marked indices removed. This supersedes the 448-sample result with video disabled; it does not qualify an Android/TV client, another source, or seek/resume behavior.

**What Jellyfin 10.11.11 can tell at Play:** the native session and fresh `MediaBrowser` header supply client name, reported version and device ID. [Jellyfin can fill missing claims from stored device data](https://github.com/jellyfin/jellyfin/blob/1fbd8739292cce610231be93daf43368733edf63/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L73-L190); Cleanyfin does not use those fallbacks. It requires a [posted device profile](https://github.com/jellyfin/jellyfin/blob/1fbd8739292cce610231be93daf43368733edf63/Jellyfin.Api/Controllers/MediaInfoController.cs#L135-L145), not Jellyfin's cached profile. Wholphin's profile name `mpv` signals the requested backend, not the engine actually running. The server can check its version, the installed plugin DLL's SHA-256 and the source bounds. It cannot verify the APK hash, decoder, hardware or actual audio/video output settings. An admin declaration suffices only in the synthetic lab; materially different production modes that cannot be distinguished get no offer. This is a compatibility gate, not client attestation or parental lockout.

Install the reviewed `delivery/evidence/register-v1.json` as `evidence-register-v1.json` in Jellyfin's private `cleanyfin-shelves-v1/` root (in the plugin configuration directory, beside `profiles/`). Install only rows measured against the exact plugin DLL, server, player and source tuple. The register is no longer embedded: embedding rows would change the DLL hash they identify. If the file is missing, invalid or unreadable, filtered offers are disabled. A changed register invalidates old grants on their next request.

In the disposable lab, put the declared APK or player build SHA-256 and exact output-mode label on two LF-delimited lines in `profiles/<viewer-id>/evidence-device.txt` under `cleanyfin-shelves-v1/`, next to the enrolled `device.txt`. Pin `LabSourceSha256` to the synthetic fixture's full digest. Only `EnableAuthRangeLab` without `EnableProductionEligibility` can return a matching experimental row, labeled in the source name and `X-Cleanyfin-Evidence` header. The runner uses an explicitly unmeasured disposable row keyed to the DLL it builds: the historical Wholphin row cannot match a different plugin build. Do not copy that test row into released evidence.

### R24: optional test playback, not full eligibility

The separate [v2 register](evidence/register-v2.json) starts empty. Nothing is newly playable on desktop mpv, the Wholphin emulator, or the onn. box. The v1 reader cannot consume a v2 row; the v2 reader accepts only `experimental` AC-3 or E-AC-3 rows on an exact tuple. AAC and FLAC are outside this tier. The admin switch does not make a diagnostic v1 measurement grant a positive result.

To review a v2 row, check the original receipt, including **all 24 cut positions and all required takes**. Each must show zero marked pictures and zero marked sound detections. A non-identical lossy decode needs an independently challenged positive detector sensitive on that take's output path. A missing take, timeout or uncalibrated negative is not a pass. Report silence, transient (pop), kept picture, A/V sync and kept sound lost separately; these do not change the full R22/R23 standard. The [schema](schema/evidence-register-v2.schema.json) checks the attestation, not the raw measurement. Install reviewed rows beside v1, as `evidence-register-v2.json`, not in the DLL. Keep each receipt tied to the DLL hash that actually ran.

[Historical desktop seam measurements](../tools/playback-lab/prototypes/common-formats/r24-historical-seams.json) record each metric's coverage separately for eight Dolby layouts on each pinned Jellyfin line. They describe earlier builds. Neither those observations nor a desktop result certify Wholphin. See the [separate TV measurement paths](../knowledge-base/01-working/common-formats-2026-11-24/04-tv-test-runbook.md) for a separate physical measurement.

The [partial R24 desktop receipt](../tools/playback-lab/prototypes/common-formats/receipt-r24-desktop-partial-2026-11-25.json.gz) covers all eight Dolby layouts on 10.11 and four on 12.4 before the job timed out. No saved take or paused seek showed a marked frame, and no saved sound detection was traced to the marked interval. Of 1,644 scheduled takes across those 12 layout/line pairs, 738 scored without a marked indication; 718 had an alarm on exactly known-kept audio (1,847 sample occurrences); 16 stopped outside the detector calibration; 172 others had no score. The last two groups cannot certify absence. None of the 12 layout/line pairs qualified. The other four 12.4 layouts remain unmeasured. `r24_breakdown.py` reproduces the counts from the receipt.
