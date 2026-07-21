#!/usr/bin/env bash
# Share the cleanyfin docs dev server over the tailnet for phone/remote viewing.
#
# Convention (see the sibling projects): the permanent `tailscale serve` on
# :8080 owns the HTTPS root `/` for another project, so cleanyfin does NOT
# take the root. Starlight's base:/cleanyfin also fights `--set-path`, so the
# clean route for docs is the raw Level-0 port over the tunnel.
set -euo pipefail

HOST="desktop-vkab06c-1.tail910574.ts.net"
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
