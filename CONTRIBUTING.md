# Contributing

Fork and clone the repository. Start with the prerequisites for the component
you're changing. None of the CI checks need a running Jellyfin instance, a
Tailscale account, or private configuration.

| Component | Prerequisites | CI commands (run in the component directory) |
| --- | --- | --- |
| Docs (`docs/`) | [Bun](https://bun.sh/) and Playwright Chromium with system dependencies | `bun install --frozen-lockfile` → `bun run build` → `bunx playwright install --with-deps chromium` → `bun run test:local` |
| Web app (`pwa/`) | Bun | `bun install --frozen-lockfile` → `bun run build` (TypeScript and Vite) |
| Server (`server/`) | [Go](https://go.dev/dl/) 1.23, as specified in [`server/go.mod`](server/go.mod) | `go vet ./...` → `CGO_ENABLED=0 go build ./...` → `go test ./...` |
| Jellyfin plugin (`plugin/`) | [.NET SDK](https://dotnet.microsoft.com/download) 9.0.x | `dotnet restore` → `dotnet build -c Release --no-restore` |

On Ubuntu with all four toolchains installed, run every CI check from the root:

```sh
sh scripts/check.sh
```

Playwright may prompt for `sudo` to install Chromium's system dependencies. The
PWA and plugin use their build as the CI check and have no separate test step.

Push your branch to your fork and open a pull request against `main`. GitHub
runs the workflows for changed components on hosted Ubuntu runners.

Optional: `bun run share:tailscale` from `docs/` starts a dev server on all
interfaces and prints your tailnet URL. It uses `tailscale` and `jq` to find the
hostname; set `TAILSCALE_HOST` to override discovery. This is not part of CI.
