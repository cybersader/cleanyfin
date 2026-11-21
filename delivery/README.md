# Playback delivery contract

`Cleanyfin.Delivery` is a pure .NET 9 library for [playback plan v1](schema/playback-plan-v1.schema.json), source/filtered clock mapping, and cut-only mpv EDL rendering. It follows the [source-bound plan, measured adapter pattern](../.claude/13-DELIVERY-PATTERN.md). It does not read media or perform authorization, token issuance, HTTP requests, or player capability checks.

Use the schema for structure and `PlanValidator` for strict wire and semantic checks. Pass independently verified source and clock facts, current authority/revision, and accepted annotation digests through `VerifiedPlanContext`. Identities in the JSON grant nothing. The caller owns freshness and revocation after parsing; the renderer needs independently verified player-clock identity and a trusted configured origin.

To add an adapter, define its accepted domain and translate the validated plan using the shared timeline. Preserve every required semantic or refuse the entire plan with a typed reason; never silently play the original. Unit tests do not establish player support. Measure and register the actual playback tuple before offering it.

From the repository root with .NET SDK 9.0.x:

```sh
dotnet test tests/Delivery.Contract.Tests --filter Category=Plan
dotnet test tests/Delivery.Contract.Tests --filter Category=Mpv
```

The golden pair uses the lab's timing with synthetic asset and authority identities; it is not proof of a live binding.
