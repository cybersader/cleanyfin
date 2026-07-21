# Documentation Style Guide

Shared convention across Cybersader projects. Keep cleanyfin's docs consistent with cyberbaser / crosswalker.

## File Naming

- Numbered orientation stubs in `.claude/` use the fixed scheme: `NN-TOPIC.md` (e.g., `01-PROBLEM.md`).
- Research/working docs in `knowledge-base/` use lowercase-kebab, optionally date-prefixed: `legal-dmca-deep-dive.md`, `2026-07-21-federation-options.md`.
- Be descriptive but concise. Make names greppable — think: *what would someone search for?*

## Document Structure

### Analysis / Research Documents
```markdown
# Title

## TL;DR
2–4 sentences: the answer up front.

## Key Findings
Tables + bullets for quick reference.

## Deep Dive
Detail, numbers, names, dates. Code/schema samples where relevant.

## Sources
Links to primary references.
```

### Technical / Design Documents
```markdown
# Title

## Overview — what this covers
## How It Works — ASCII diagrams, flow
## Implementation — schemas, API refs, code
## Limitations / Trade-offs — honest assessment
## Recommendations — actionable guidance
```

## Formatting Preferences

- Tables for comparisons.
- Code blocks for anything technical (even pseudo-code).
- **ASCII diagrams over external images** — portable, git-friendly.
- Keep files focused — split rather than write one giant doc.
- Link between related files with relative paths.

## Content Philosophy

- **Exhaustive over brief** — this is a knowledge base, not a pitch deck.
- **Honest about limitations** — don't oversell an approach; name the risk.
- **Cite sources** — link where the info came from; distinguish fact from lean.
- **Simplify first** — for a project whose north star is "super easy to set up," prefer the boring, proven, low-maintenance option and say why.
