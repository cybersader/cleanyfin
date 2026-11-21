# Playback delivery contract

`Cleanyfin.Delivery` is a pure .NET 9 library for [playback plan v1](schema/playback-plan-v1.schema.json), source/filtered clock mapping, and cut-only mpv EDL rendering. It follows the [source-bound plan, measured adapter pattern](../.claude/13-DELIVERY-PATTERN.md). It does not read media or perform authorization, token issuance, HTTP requests, or player capability checks.

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
