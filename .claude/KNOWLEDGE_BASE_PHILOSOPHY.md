# Knowledge Base Philosophy

## Core Principle

**All Cybersader projects follow a living markdown knowledge base pattern, designed for both human and LLM-agent collaboration.**

The KB is not documentation created after the fact — it IS the project's brain. It evolves as understanding deepens, decisions are made, and implementation progresses. It is persistent memory across sessions and agents.

---

## Why This Pattern

### For Humans
- **Searchable** — grep, Obsidian, VS Code all work
- **Portable** — no vendor lock-in, works everywhere
- **Git-friendly** — diffs, history, collaboration
- **Human-readable** — no special tools needed

### For LLM Agents
- **Context loading** — an agent reads relevant files at session start
- **Persistent memory** — knowledge survives session boundaries
- **Structured output** — research/analysis goes INTO files, not just chat
- **Multi-agent** — different agents work on the same KB
- **Auditable** — you can see what an agent wrote/changed

---

## The Two Layers

cleanyfin uses the same two-layer split as the sibling projects (cyberbaser, crosswalker):

1. **`.claude/` — the orientation + pointer layer.** Numbered greppable stubs that summarize current truth. A fresh agent reads `PROJECT_CONTEXT.md` → `FOCUS.md`, then follows pointers for depth. This layer is the fast on-ramp; keep it lean and current.
2. **`knowledge-base/` — the working research corpus (temperature gradient).** Raw research, deep dives, and synthesis land here and mature over time (see the temperature gradient below). When the project grows a docs site, that becomes the third, *canonical* layer (as in cyberbaser) — until then, `knowledge-base/` is canonical for depth.

## Temperature Gradient (knowledge-base/)

Knowledge flows from hot/raw → cool/settled:

| Folder | Temperature | Holds |
|---|---|---|
| `00-inbox/` | 🔥 hot | Raw captures, dumps, unprocessed research output, quick notes |
| `01-working/` | 🌤️ warm | Actively-developed docs, deep dives, ADRs, synthesis in progress |
| `04-archive/` | ❄️ cold | Superseded or historical material kept for provenance |

Promote a file by moving it down the gradient as it settles. Don't let `00-inbox/` become a graveyard — process it into `01-working/`.

---

## Working With Agents

### Session Start
```
1. Agent reads .claude/PROJECT_CONTEXT.md then FOCUS.md for direction
2. Agent reads the numbered stub(s) relevant to the task
3. Agent follows pointers into knowledge-base/ for depth
```

### During Work
- **UPDATE** KB files as new information is learned
- **CREATE** new files for new topics (don't dump in chat)
- **LINK** between related files
- **DELETE** obsolete content (git keeps history)

### Research Tasks
**Output goes INTO the KB, not just displayed in chat.**
```
Bad:  "Here's what I found about DMCA: [wall of text in chat]"
Good: "I've written knowledge-base/01-working/legal-dmca-deep-dive.md."
```

### Locked Decisions
When a decision gets locked in a session, **propagate it in the same session**: record it in `41-QUESTIONS-RESOLVED.md`, and if it changes direction, update `PROJECT_CONTEXT.md` and `FOCUS.md` too. This layer going stale is how agents end up pointed in an old direction.
