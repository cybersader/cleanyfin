# cleanyfin docs

The public docs site — Astro + [Starlight](https://starlight.astro.build/), publishing the cleanyfin knowledge base. Content lives in `src/content/docs/`; it is derived from the source-of-truth `.claude/` orientation layer and `knowledge-base/` deep-dives one directory up.

## Run it

```bash
cd docs
bun run dev            # http://localhost:4321/cleanyfin/  (preflight auto-installs deps)
bun run dev:host       # bind 0.0.0.0 for LAN / Tailscale
bun run share:tailscale # phone-view over the tailnet (Level-0 raw port)
bun run build          # production build -> dist/
bun run preview        # serve the built output
bun run test:local     # headless Playwright smoke tests (build first)
```

The `preflight` step auto-installs dependencies and heals WSL↔Windows `node_modules` platform mismatches, so `bun run dev` works on a fresh checkout with no separate `bun install`.

## Deploy (GitHub Pages)

Two GitHub Actions workflows live in `.github/workflows/`:

- **`ci.yml`** — on every PR/push touching `docs/**`: `bun install` → `bun run build` → Playwright smoke tests. The "is it still up?" gate.
- **`deploy-docs.yml`** — on push to `main` touching `docs/**` (or manual dispatch): builds `docs/dist` and deploys to Pages via OIDC.

**One-time setup after the repo is pushed:** in the repo **Settings → Pages**, set **Source = "GitHub Actions"** (not "Deploy from a branch"). Then every `docs/**` change on `main` publishes to `https://cybersader.github.io/cleanyfin/`. The site is a project page — `astro.config.mjs` already sets `site` + `base: '/cleanyfin'`, so no path rewriting is needed.

## Conventions

- **Base path** is `/cleanyfin` (GitHub Pages project-site style).
- **Sidebar** auto-generates from the `vision/`, `design/`, `project/`, `research/` directories via each page's frontmatter `sidebar.order`.
- **Diagrams are ASCII** (portable, git-friendly) — no mermaid/browser render dependency, keeping the build fast and resilient.
- Obsidian callouts (`> [!note]`) and `[[wikilinks]]` are supported in markdown.
