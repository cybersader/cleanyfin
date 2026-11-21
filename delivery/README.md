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
```

The golden pair uses the lab's timing with synthetic asset and authority identities; it is not proof of a live binding.

## Evidence register

Each [row](evidence/register-v1.json) names one exact adapter revision, app version/build, backend, device/output mode, server/plugin build and source layout. It lists actions, controls and marked/kept picture and sound outcomes, with capture limits and an evidence path. `null` is an exact missing value, not a wildcard. Production requires `eligible`, all four outcomes passing and all five controls exercised. **Nothing qualifies today.** Support claims in docs come from this register, not from DirectPlay flags or successful API tests.

Run `python3 tools/playback-lab/check-plugin.py --case eligibility` to check admission, refusals and revocation in the disposable pinned lab. To add a measured row, use the [hard fixture and scorer](../tools/playback-lab/README.md#shared-hard-fixture-compare-playback-paths) on the actual tuple. Record the positive uncut control, straight cut, forward/back seeks, persisted resume and restart; score marked and kept picture and PCM, including the join; record latency and detection limits. Keep partial results experimental and link the original receipt. `dotnet test tests/Delivery.Contract.Tests --filter Category=Eligibility` checks the [closed schema](schema/evidence-register-v1.schema.json), reader and admission. The check for an existing eligible-row evidence path is a review tripwire, not a security control or proof of measurement.

**What Jellyfin 10.11.11 can tell at Play:** the native session and fresh `MediaBrowser` header supply client name, reported version and device ID. [Jellyfin can fill missing claims from stored device data](https://github.com/jellyfin/jellyfin/blob/1fbd8739292cce610231be93daf43368733edf63/Jellyfin.Server.Implementations/Security/AuthorizationContext.cs#L73-L190); Cleanyfin does not use those fallbacks. It requires a [posted device profile](https://github.com/jellyfin/jellyfin/blob/1fbd8739292cce610231be93daf43368733edf63/Jellyfin.Api/Controllers/MediaInfoController.cs#L135-L145), not Jellyfin's cached profile. Wholphin's profile name `mpv` signals the requested backend, not the engine actually running. The server can check its version, the installed plugin DLL's SHA-256 and the source bounds. It cannot verify the APK hash, decoder, hardware or actual audio/video output settings. An admin declaration suffices only in the synthetic lab; materially different production modes that cannot be distinguished get no offer. This is a compatibility gate, not client attestation or parental lockout.

In the disposable lab, put the declared APK SHA-256 and exact output-mode label on two LF-delimited lines in `profiles/<viewer-id>/evidence-device.txt` under `cleanyfin-shelves-v1/`, next to the enrolled `device.txt`. Pin `LabSourceSha256` to the synthetic fixture's full digest. Only `EnableAuthRangeLab` without `EnableProductionEligibility` can return a matching experimental row, labeled in the source name and `X-Cleanyfin-Evidence` header. A local `evidence-register-v1.json` in the private shelf root overrides the packaged register; if invalid or unreadable, it disables offers. A changed register invalidates old grants on their next request. The runner uses an explicitly unmeasured disposable row keyed to the DLL it builds: the historical Wholphin row cannot match a different plugin build. Do not copy that test row into released evidence.
