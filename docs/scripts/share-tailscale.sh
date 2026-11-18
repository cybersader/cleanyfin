#!/usr/bin/env bash
# Share the cleanyfin docs dev server over the tailnet for phone/remote viewing.
set -euo pipefail

HOST="${TAILSCALE_HOST:-}"
if [[ -z "$HOST" ]]; then
  if ! command -v tailscale >/dev/null || ! command -v jq >/dev/null; then
    echo 'Install tailscale and jq, or set TAILSCALE_HOST to your tailnet hostname.' >&2
    exit 1
  fi
  if ! HOST=$(tailscale status --json | jq -er '.Self.DNSName | strings | rtrimstr(".") | select(length > 0)'); then
    echo 'Could not find a tailnet hostname. Connect Tailscale or set TAILSCALE_HOST.' >&2
    exit 1
  fi
fi
HOST="${HOST%.}"
PORT="${PORT:-4321}"

cat <<EOF

  ================================================================
   cleanyfin docs — shared over Tailscale (Level 0, raw port)
   Local:    http://localhost:${PORT}/cleanyfin/
   Tailnet:  http://${HOST}:${PORT}/cleanyfin/
   (Open the Tailnet URL from any device on your tailnet.)
   Ctrl+C to stop.
  ================================================================

EOF

exec astro dev --host 0.0.0.0 --port "${PORT}"
