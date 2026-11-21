#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

(cd docs && bun install --frozen-lockfile && bun run build &&
  bunx playwright install --with-deps chromium &&
  env -u TEST_URL CI=1 bun run test:local)
(cd pwa && bun install --frozen-lockfile && bun run build)
(cd server && go vet ./... && CGO_ENABLED=0 go build ./... && go test ./...)
(cd plugin && dotnet restore && dotnet build -c Release --no-restore)
(cd tests/Delivery.Contract.Tests && dotnet test --filter Category=Plan && dotnet test --filter Category=Mpv)
