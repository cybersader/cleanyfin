# cleanyfin-pwa

The companion **marking app** — stamp in/out points on what you're watching and
submit them to the cleanyfin API (the SponsorBlock-style separate submission
flow, decision R14). Vite + TypeScript, built and run with `bun`.

## Run

```bash
cd pwa
bun install
bun run dev       # http://localhost:5173
bun run build     # tsc --noEmit && vite build -> dist/
bun run preview   # serve the built dist/
```

## Mark while watching

The top card is the household marking path ([design](../knowledge-base/01-working/marking-foundation-2026-11-24/01-marking-foundation-design.md)):

- **Mark** saves "something around here" with no category; **Language / Violence / Sex / Nudity / Other** save a moment with that hint; **Start … End** saves a span (a category tapped while it is open names the span).
- Each press becomes one [capture-v1](../delivery/schema/capture-v1.schema.json) in this browser's IndexedDB. The position is the session's last report plus the time since `LastPlaybackCheckIn`, and the search window widens by that age. The TV is not paused (R18).
- **Export captures** downloads a JSON array of captures for the reviewer, who sets exact bounds with `cleanyfin-mark review` (see [delivery/README](../delivery/README.md#marking)). A capture never filters anything by itself.
- Captures stay on the phone until exported; clearing site data deletes them. Slice 2 sends them to the household store instead.

`bun test` checks the capture builder.

## How it works (shared-hub submission)

1. Enter your **Jellyfin** server URL + an access token, and the **cleanyfin API**
   URL + a submitter id, then **Connect**.
2. The app polls Jellyfin `GET /Sessions` (header `X-Emby-Token`) once a second,
   finds the session with a `NowPlayingItem`, and shows the live position
   (`PlayState.PositionTicks / 10000` = ms).
3. **Mark IN** / **Mark OUT** capture the current position; pick a category,
   severity, and action; **Submit** POSTs to `{api}/api/v1/segments` with
   `fingerprint = "jf:" + <ItemId>` — the same scheme the plugin reads.

## Honest limits (this slice)

- **CORS:** the browser must be allowed to call both servers cross-origin. The
  cleanyfin API sends permissive CORS headers by default (dev), and Jellyfin's
  CORS policy may need your PWA origin added. If a request fails with a network
  error, that's usually CORS.
- **Fingerprint is a placeholder** (`jf:` + ItemId), matching the plugin — real
  moviehash calibration (R04) is a later slice, so marks apply per Jellyfin item.
- **Auth token** is kept only in the page (never stored); this is a minimal dev
  tool, not a hardened client.
- No service worker / icons yet — it's a "PWA" in the installable-manifest sense
  only for now.
