# cleanyfin-api

The crowdsourced content-segment API — the source-of-truth hub for cleanyfin
(decision R02). The Jellyfin plugin and the marking PWA are thin clients of this
API. It stores and serves **only** timestamps + category metadata (R01), never
audio/video.

Go single static binary · pure-Go SQLite (WAL) · configured by env vars.

## Run it

```bash
# from the repo root — the golden path:
docker compose up -d --build          # http://localhost:8080
docker compose logs -f api
docker compose down                   # data persists in the cleanyfin-data volume
```

No local Go toolchain needed — the image builds the binary. Backup = copy the
SQLite file from the `cleanyfin-data` volume.

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `CLEANYFIN_ADDR` | `:8080` | listen address |
| `CLEANYFIN_DB` | `/data/cleanyfin.db` | SQLite file path |
| `CLEANYFIN_PORT` | `8080` | (compose) host port to publish |

## API (v1)

| Method + path | Purpose |
|---|---|
| `GET /healthz` | liveness (`ok`) |
| `GET /readyz` | readiness (DB reachable) |
| `GET /api/v1/stats` | segment counts |
| `GET /api/v1/segments?fp=<fingerprint>` | visible segments for a release fingerprint (auto-hidden at votes ≤ −2, R08) |
| `POST /api/v1/segments` | submit a segment |
| `POST /api/v1/segments/{id}/vote` | up/down vote (`value`: `1` or `-1`; one per submitter) |

### Submit body

```json
{
  "fingerprint": "oshash:abc123+7200000",
  "durationMs": 7200000,
  "startMs": 723000,
  "endMs": 729000,
  "category": "profanity",
  "severity": 2,
  "action": "mute",
  "submitterId": "pseudonymous-id"
}
```

`category` ∈ {profanity, sexual_dialogue, sex_scene, nudity, violence, gore,
disturbing, substance_use, crude} (R05). `action` ∈ {mute, skip, mark} (R06;
blur/crop schema-reserved, rendered as skip in v1). `severity` ∈ 0–3.

## Scope of this slice

This is Phase-3 slice 1: the backbone. Deliberately **not** yet included —
release/title tables + fingerprint calibration tiers (R04), the SponsorBlock-style
hash-prefix privacy query (R08), curator/profile tables (R09), public data
dumps + mirrors (R03), and the plugin/PWA clients. Those are the next slices.
