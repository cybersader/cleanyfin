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

## How it works

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
