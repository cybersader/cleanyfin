# Tech Stack & DevOps for super-easy setup + resilience

> Deep-dive from the 2026-07-21 research fan-out (workflow `cleanyfin-research`, opus, web-sourced). Lightly formatted raw findings — promote/condense into `.claude/` stubs as decisions lock. Confidence + sources preserved.

## TL;DR

For "a non-expert self-hosts in 5 minutes and it won't fall over," the winning shape is a SINGLE self-contained artifact plus a single golden-path docker-compose.yml. My concrete pick: write the segment API in Go, compiled to one static binary that ALSO embeds the PWA's static files (Go embed.FS) so one process serves both API and UI; use SQLite in WAL mode as the default database (one file = zero-ops, backup is copying a file), with Litestream as an optional sidecar for continuous off-box backup; deploy via one docker-compose.yml (restart: unless-stopped, a /healthz healthcheck, one named volume, one .env) with a "download-the-binary + systemd" path for people who don't want Docker at all. The Jellyfin plugin is unavoidably C#/.NET (Jellyfin plugins are .NET assemblies — 10.10.x = net8.0, 10.11.x = net9.0) and ships through a GitHub-hosted plugin manifest.json repo built by GitHub Actions; that is the ONLY place you're forced off your primary language, so keep the plugin thin (a client of the API) and put all logic in the Go server. Graduate SQLite→Postgres only at SponsorBlock scale (millions of segments / heavy concurrent writes), not before. This maximizes "single binary, no runtime deps, copy-a-file backup, local-first keeps working if a federation peer is down."

## Key Findings

### 1. Jellyfin plugins are .NET assemblies, so the plugin component MUST be C#/.NET — but nothing else has to be  ·  🟢 high

Jellyfin loads plugins as .NET DLLs with a meta.json manifest. The target framework tracks the server: 10.10.x is built on dotnet 8.0.x (net8.0) and 10.11.x moved to dotnet 9.0.x (net9.0). This means the in-server plugin is the one component you cannot write in another language. The correct architecture is therefore a THIN plugin (reads per-profile filter settings, pulls the edit-decision list for the currently-playing title from the cleanyfin API, applies skips/mutes) with all crowdsourcing/DB/federation logic living in a separate server you're free to write in any language. Do not try to make the plugin the database or the API.

Sources: <https://jellyfin.org/posts/jellyfin-release-10.11.0/> · <https://github.com/jellyfin/jellyfin-plugin-template/blob/master/build.yaml> · <https://www.nuget.org/packages/Jellyfin.Model>

### 2. SponsorBlock — the closest architectural analogy — runs Node.js/TypeScript and supports BOTH Postgres and SQLite  ·  🟢 high

SponsorBlockServer (github.com/ajayyy/SponsorBlockServer) is a Node.js + TypeScript server that 'uses a Postgres or Sqlite database to hold all the timing data,' configured via a config.json (copied from config.json.example), with a Dockerfile and docker-compose files present. It uses Redis for caching at scale. The public production instance runs Postgres because of scale (the downloadable DB at sponsor.ajay.app/database is very large), but the codebase explicitly still supports SQLite. Takeaway for cleanyfin: SQLite is a legitimate first-class option even in the reference project; Postgres is a scale decision, not a correctness requirement.

Sources: <https://github.com/ajayyy/SponsorBlockServer> · <https://sponsor.ajay.app/database>

### 3. Pure-Go SQLite (modernc.org/sqlite) removes the last obstacle to a single static binary with zero runtime dependencies  ·  🟢 high

Historically SQLite in Go meant mattn/go-sqlite3, which needs CGo and a C compiler at build time (breaks Alpine images, cross-compiles, restricted CI). modernc.org/sqlite is a CGo-free transpiled port of SQLite3 that is a standard database/sql driver; as of v1.31.0 (2024-07-22) it even supports windows/386. With it you 'just go build a single Windows/Mac/Linux executable on the same machine and ship it.' Combined with Go's embed.FS (bake the compiled PWA into the same binary), cleanyfin's entire server + UI can be ONE file with no interpreter, no node_modules, no libc dependency — the strongest possible 'super easy' story.

Sources: <https://pkg.go.dev/modernc.org/sqlite> · <https://practicalgobook.net/posts/go-sqlite-no-cgo/> · <https://hiandrewquinn.github.io/til-site/posts/you-don-t-need-cgo-to-use-sqlite-in-your-go-binary/>

### 4. Litestream turns a single SQLite file into a disaster-recoverable production store without adding a database server  ·  🟢 high

Litestream (Go, 13k+ stars) runs as a background process, hooks SQLite's WAL, and continuously streams incremental changes to S3/GCS/Azure/SFTP/local paths. Because it goes through the SQLite API rather than copying raw files, it avoids corruption and needs no app downtime; restore is a single command. Critical limitation to state honestly: it is DISASTER RECOVERY, not high availability — replication is async (you can lose the last ~second of un-shipped writes on disk death), and 0.5.x enforces one replica destination per DB. For a single-node, read-heavy crowdsourced-segment service this is exactly the right resilience tier: no cluster, no k8s, but a durable off-box backup. For the truly minimal setup you can skip even this and rely on a nightly 'sqlite3 .backup' / file copy cron.

Sources: <https://litestream.io/how-it-works/> · <https://litestream.io/getting-started/> · <https://news.ycombinator.com/item?id=38837870>

### 5. The Jellyfin plugin-distribution path is a static manifest.json repo, fully automatable in GitHub Actions — no special hosting needed  ·  🟢 high

Jellyfin clients add a plugin repo by URL pointing at a manifest.json containing guid, name, and a versions[] array where each entry has targetAbi, sourceUrl (the release .zip), checksum (md5 of the zip), and timestamp. Jellyfin's docs state there is 'no requirement for any specific method of hosting these files' — GitHub Releases + a manifest.json on GitHub Pages / raw is sufficient. A release Action builds the plugin, zips the DLL, computes the md5, updates manifest.json, and attaches artifacts to the GitHub Release. Ready-made building blocks exist: the official jellyfin/jellyfin-plugin-template (build.yaml with targetAbi/framework/artifacts) and Kevinjil/jellyfin-plugin-repo-action, which auto-generates the repo manifest from release assets (.zip + .md5 + build.yaml). This keeps plugin CI minimal and reproducible.

Sources: <https://github.com/jellyfin/jellyfin-plugin-template/blob/master/build.yaml> · <https://github.com/Kevinjil/jellyfin-plugin-repo-action> · <https://jellyfin.org/posts/plugin-updates/>

### 6. The companion marking UI can read live playback position straight from the Jellyfin API via /Sessions PlayState.PositionTicks  ·  🟡 medium

GET /Sessions returns active sessions; each has a PlayState object whose PositionTicks gives the current playback position. Jellyfin uses .NET ticks (1 tick = 100 nanoseconds → 10,000,000 ticks per second), so seconds = PositionTicks / 10,000,000 (note: some third-party write-ups state the conversion incorrectly — verify against the official/SDK docs). The marking UI does not need to sit inside the video pipeline: a PWA can poll /Sessions (or use the official @jellyfin/sdk PlaystateApi), grab the current NowPlayingItem + PositionTicks to stamp in/out points, and POST the finished segment (title/version id, start, end, category) to the cleanyfin API. This keeps the marking client decoupled and cross-platform.

Sources: <https://typescript-sdk.jellyfin.org/functions/generated-client.PlaystateApiFp.html> · <https://deepwiki.com/jellyfin/jellyfin-meta/3.2-openapi-specification:-endpoint-domains-and-playback-lifecycle>

### 7. The docker-compose 'golden path' resilience pattern is well-established and needs no orchestrator  ·  🟢 high

The consensus self-hosting pattern: one declarative compose.yaml using restart: unless-stopped (auto-recovers on crash/daemon-restart but respects a manual docker stop), a HEALTHCHECK wired to a /healthz endpoint, a named volume for data, secrets in a git-ignored .env, and images pinned to a tag+digest. Important nuance to encode correctly: Compose does NOT auto-restart a container just because its healthcheck reports 'unhealthy' — the restart policy fires on process EXIT. So the resilience recipe is: (1) restart: unless-stopped for crash recovery, (2) healthcheck for status visibility + depends_on gating, and if you want auto-kill-on-unhealthy add a lightweight watchtower/autoheal sidecar (optional, not required for MVP). This gives 'good-enough' resilience with zero k8s.

Sources: <https://last9.io/blog/docker-compose-health-checks/> · <https://oneuptime.com/blog/post/2026-02-08-how-to-use-docker-compose-restart-policy-options/view> · <https://hometechops.com/self-hosting/maintainable-docker-compose-home-stack>

### 8. Go stdlib now covers structured logging and routing, keeping the dependency tree tiny  ·  🟢 high

Since Go 1.21 the standard library ships log/slog for structured (JSON) logging, and Go 1.22 added method+wildcard routing to net/http's ServeMux — so a small API can be built with essentially zero third-party web/logging deps (chi is a reasonable thin add if you want middleware). Fewer deps = fewer CVEs, fewer breaking upgrades, less maintenance — directly serving the 'low-maintenance side project' constraint. This is a concrete advantage over Node/Python where a real project pulls in dozens of transitive packages.

Sources: <https://pkg.go.dev/modernc.org/sqlite> · <https://litestream.io/getting-started/>

## Recommendations for cleanyfin

**R1. Write the segment API + query/submission server in Go, compiled to ONE static binary that also embeds the PWA static assets via embed.FS. Ship it three ways from the same build: (a) a container image on GHCR, (b) a raw binary + systemd unit, (c) a docker-compose.yml. This is your 'super easy' backbone: no interpreter, no runtime deps, no libc pinning.**

- *Why:* Go's single-static-binary + cross-compile story is the lowest-friction self-host artifact that exists; modernc.org/sqlite makes it fully CGo-free so one machine builds all platforms. Embedding the UI means one process, one port, one thing to run — a non-expert never touches Node, Python, or a separate web server. The stdlib now covers logging (slog) and routing, so the dep tree stays maintainable for a side project.
- *Risk / tradeoff:* The team may be more fluent in C#/TypeScript, and you already need C# for the plugin — so Go adds a third language (Go server, C# plugin, JS/TS PWA). Mitigate by keeping the plugin and PWA thin clients of the Go API. If the team strongly prefers one language, the honest fallback is .NET for the server too (matches the plugin, ships a self-contained single-file publish), but its self-contained binaries are larger and startup/footprint heavier than Go's; Node (like SponsorBlock) is fine but drags node_modules and a runtime into every deploy.

**R2. Default the database to SQLite in WAL mode, one file on a mounted volume. Document backup as literally 'stop-free copy of the file' plus an OPTIONAL Litestream sidecar for continuous off-box replication to any S3-compatible/SFTP/local target. Explicitly define the graduation trigger to Postgres.**

- *Why:* SQLite is zero-ops: no separate DB container, no connection strings, no tuning; backup/restore is a file operation, which is the most resilient thing a non-expert can be asked to do. SponsorBlock itself supports SQLite in-code, proving it's viable for this exact data shape (timestamped segments). Litestream adds durable disaster recovery without introducing a database server or a cluster. State the graduation rule plainly: move to Postgres only when you hit millions of segments AND sustained concurrent writers / multiple app instances — i.e. SponsorBlock-scale — not before.
- *Risk / tradeoff:* SQLite is single-writer; heavy concurrent submission bursts serialize writes. WAL mode mitigates read/write contention but not many-writer workloads. Litestream is async DR, not HA — a disk-death loses the last ~second of writes and it supports one replica target per DB. If you outgrow single-node you must migrate schema to Postgres; design the schema portably (avoid SQLite-only quirks) from day one to make that migration cheap.

**R3. Keep the Jellyfin plugin as a THIN C#/.NET client of the Go API. Target the current stable ABI (net8.0 for 10.10.x, net9.0 for 10.11.x — pick based on your minimum supported server) and distribute via a static manifest.json plugin repo generated by GitHub Actions (jellyfin-plugin-template + Kevinjil/jellyfin-plugin-repo-action), hosted on GitHub Pages/Releases.**

- *Why:* Plugins MUST be .NET, but that's the only forced language boundary — putting all logic server-side means the plugin rarely changes and stays trivial to maintain across Jellyfin ABI bumps. The manifest.json-over-GitHub distribution needs no special hosting and is fully automatable, giving reproducible, checksummed, versioned releases users add with a single repo URL.
- *Risk / tradeoff:* Jellyfin's ABI moves (net8→net9 across 10.10→10.11) can force plugin rebuilds and you may need to publish multiple targetAbi entries in the manifest to support users on different server versions. Plugin APIs for intercepting/skipping playback are less documented than the REST API — validate early that a plugin can actually enforce skips/mutes per-profile (this is a technical-feasibility spike worth doing before committing).

**R4. Build the marking UI as an installable PWA that talks to the Jellyfin REST API (/Sessions → PlayState.PositionTicks, converting ticks/10,000,000 = seconds) to capture in/out points and POSTs segments to the cleanyfin API. Keep the framework boring: SvelteKit with adapter-static (compiles to plain static files you embed in the Go binary), or, for maximum minimalism, plain HTML + htmx/Alpine.js.**

- *Why:* A PWA is one codebase for mobile + desktop, installable, no app-store friction — ideal for 'mark segments while watching.' adapter-static output embeds cleanly into the Go binary (single-artifact goal). Reading position from the documented /Sessions endpoint avoids coupling the marking flow into the video pipeline and works from any device on the network.
- *Risk / tradeoff:* Reading another session's live position assumes the marking device and the playback device are the same Jellyfin user/session and that polling latency is acceptable for frame-accurate marks — users may need to nudge timestamps manually. Verify the exact tick→seconds conversion against the official SDK (some third-party docs get it wrong). If the team already knows React, React+Vite is an acceptable substitute; the priority is 'proven + static-exportable,' not the specific framework.

**R5. Ship a single golden-path docker-compose.yml as the headline install: restart: unless-stopped, a HEALTHCHECK against /healthz, one named volume for the SQLite file, one documented .env with sane defaults, images pinned to tag+digest. Provide a one-liner (curl an install script or 'docker compose up -d') and a no-Docker 'download binary + systemd unit' alternative.**

- *Why:* This is the well-trodden self-hosting resilience pattern that recovers from crashes and reboots without any orchestrator — the maintainer explicitly does NOT want k8s/hyperscalers. One compose file + one .env is genuinely a 5-minute setup. The systemd path serves users who won't install Docker at all.
- *Risk / tradeoff:* Compose does NOT auto-restart a container merely because its healthcheck is 'unhealthy' (restart fires on EXIT) — so a hung-but-not-crashed process won't self-heal unless you add an optional autoheal/watchtower sidecar; document this honestly rather than implying the healthcheck alone guarantees recovery. Digest-pinning images improves reproducibility but requires a deliberate bump process for updates.

**R6. Make config env-var-first with sane defaults and a single documented .env.example; make federation strictly LOCAL-FIRST and pull-based, degrading gracefully when a peer is unreachable. Use structured JSON logs (slog), a /healthz (liveness) + /readyz (DB reachable) endpoint pair, and either Litestream or a nightly file-copy cron for backups.**

- *Why:* Env vars + one .env is the least-surprising config surface for self-hosters and plays perfectly with Docker/systemd. Local-first federation means a node keeps serving its own segment DB even if every peer is down — the resilience property that matters most for a decentralized/subsidiarity design. slog + health endpoints give observability without a metrics stack; a backup cron is the floor of data safety.
- *Risk / tradeoff:* Pull-based federation can serve stale data if a peer is long-offline and needs conflict/merge rules for segments contributed in multiple places (dedup by title-version + timestamp range + category, with moderation/voting like SponsorBlock). Env-var config gets unwieldy if the option set grows — keep the surface small and resist adding knobs. Health endpoints and logs are necessary but not a full observability solution if the project ever scales.

## Open Questions

- **Primary server language: Go (best single-binary/deploy story, but a 3rd language alongside the C# plugin and JS PWA) vs .NET (one language with the plugin, self-contained single-file publish, but heavier artifacts) vs Node/TS (matches SponsorBlock, but runtime + node_modules in every deploy)?** — *lean:* Go for the deploy/resilience win, with the plugin and PWA kept as thin clients so the language count doesn't hurt. If the team's fluency is decisively C#, .NET server is the defensible one-language fallback.
- **Should Litestream be in the default docker-compose golden path, or an opt-in 'advanced backup' add-on so the 5-minute install has zero external-storage config?** — *lean:* Opt-in. Default install = SQLite file on a volume + a nightly local file-copy/.backup cron; document Litestream as the one-step upgrade for off-box durability. Keeps the headline path dependency-free.
- **Which Jellyfin ABI is the minimum supported target — net8.0 (10.10.x) or net9.0 (10.11.x) — and do you publish multiple targetAbi entries to cover both?** — *lean:* Support the current stable line (10.11.x / net9.0) as primary and add a 10.10.x/net8.0 manifest entry only if user demand appears; avoid maintaining more ABI targets than you must.
- **Can a Jellyfin plugin actually enforce per-profile skip/mute on the server-side playback path (vs. needing client-side cooperation)? This determines whether the plugin is the enforcement point or just a settings/EDL provider.** — *lean:* Run a small feasibility spike against 10.11 before committing architecture; if server-side enforcement is limited, fall back to an EDL-delivery model that cooperating clients apply (SponsorBlock-style), which is also cleaner for DMCA safety.
- **PWA framework: SvelteKit (adapter-static, embeds nicely, low-maintenance) vs plain HTML+htmx/Alpine (least code) vs React (team familiarity)?** — *lean:* SvelteKit with adapter-static if you want a real app UI; htmx if the marking flow stays simple. Both static-export into the Go binary.

## Sources

- [ajayyy/SponsorBlockServer (GitHub)](https://github.com/ajayyy/SponsorBlockServer) — Reference architecture: Node.js/TypeScript segment server supporting BOTH Postgres and SQLite, config.json, Dockerfile/compose, Redis at scale.
- [Jellyfin 10.11.0 release notes](https://jellyfin.org/posts/jellyfin-release-10.11.0/) — Confirms 10.11.x is built on .NET 9 (10.10.x = .NET 8); sets the plugin targetAbi/framework you must match.
- [jellyfin/jellyfin-plugin-template build.yaml](https://github.com/jellyfin/jellyfin-plugin-template/blob/master/build.yaml) — Official plugin scaffold: build.yaml with targetAbi, framework (net8.0/net9.0), artifacts, changelog — the starting point for the C# plugin.
- [Kevinjil/jellyfin-plugin-repo-action](https://github.com/Kevinjil/jellyfin-plugin-repo-action) — GitHub Action that auto-generates the Jellyfin plugin repo manifest.json from release assets (.zip + .md5 + build.yaml) — minimal plugin CI/CD.
- [Jellyfin plugin repositories docs](https://jellyfin.org/posts/plugin-updates/) — manifest.json fields (guid, versions[], targetAbi, sourceUrl, checksum, timestamp) and that any hosting method is acceptable (GitHub Pages/Releases works).
- [modernc.org/sqlite (Go package)](https://pkg.go.dev/modernc.org/sqlite) — CGo-free pure-Go SQLite database/sql driver — enables a single static, cross-compiled Go binary with no C toolchain.
- [Using SQLite from Go without CGo (Practical Go)](https://practicalgobook.net/posts/go-sqlite-no-cgo/) — Practical guidance on shipping SQLite in a pure-Go binary; why avoiding CGo matters for easy builds/deploys.
- [Litestream — How it works / Getting started](https://litestream.io/how-it-works/) — Continuous WAL-based SQLite replication to S3/GCS/SFTP/local; disaster recovery (not HA), async, one replica per DB — the resilience add-on for a single SQLite file.
- [Docker Compose health checks (Last9)](https://last9.io/blog/docker-compose-health-checks/) — Healthcheck syntax and the key gotcha: Compose won't auto-restart an 'unhealthy' container without a restart policy / autoheal sidecar.
- [Docker Compose restart policies (OneUptime)](https://oneuptime.com/blog/post/2026-02-08-how-to-use-docker-compose-restart-policy-options/view) — restart: unless-stopped semantics — auto-recover on crash/reboot but respect manual stops; the right default for self-hosted resilience.
- [A maintainable Docker Compose home stack (HomeTechOps)](https://hometechops.com/self-hosting/maintainable-docker-compose-home-stack) — Golden-path self-host pattern: one compose.yaml, .env in gitignore, named volumes, pinned images, healthchecks, back up compose+.env+data together.
- [Jellyfin TypeScript SDK — PlaystateApi](https://typescript-sdk.jellyfin.org/functions/generated-client.PlaystateApiFp.html) — Official SDK for playback state; /Sessions PlayState.PositionTicks gives current position (ticks = 100ns; /10,000,000 = seconds) for the marking PWA.
