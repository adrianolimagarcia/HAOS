---
name: haos-codebase-wiki
description: Consulta o mapa de código do HAOS antes de arquitetura.
version: 1.0.0
author: Adriano Lima + Hermes Agent
license: MIT
platforms: [linux, macos]
category: autonomous-ai-agents
tags: [haos, codebase, wiki, graph, navigation, architecture]
---

# HAOS Codebase Wiki Skill

Navigates the HAOS/ Hermes codebase through the persistent code graph produced by
`hermes codebase-wiki`. It answers *structural* questions — where a concept lives, what a module
depends on, who calls a function, which areas bridge to which — from local, deterministic data.
It does not answer *semantic* questions (what a function does line by line); those still need
`read_file` on the actual sources.

## When to Use

Use this skill before answering any architecture, dependency, or "where is X implemented"
question about the Hermes/HAOS tree. Also use it when planning a refactor to check blast radius
(god nodes, bridges) or when choosing where a new capability should live.

Skip it when the question is about a tiny file you already know, or when the wiki has not been
generated yet (generate it first — see Prerequisites).

## Prerequisites

- A generated wiki under `~/.hermes/codebase-wiki/` (profile-aware, `get_hermes_home()`).
  Generate or refresh it with `terminal` (re-running is the update; the cache
  re-parses only changed files):
  ```bash
  hermes codebase-wiki .
  ```
  Optionally add `--docs` to index `.md` concept nodes too, or `--watch` to
  re-index automatically while editing.
- `read_file`, `search_files`, and `terminal` for the steps below.

## How to Run

1. Read the map's front door: `read_file` on `~/.hermes/codebase-wiki/index.md` (via
   `get_hermes_home()` in scripts; never hardcode the home path).
2. For a specific area, `read_file` the matching article in
   `~/.hermes/codebase-wiki/modules/` (e.g. `modules/tools.md`).
3. For precise edge queries (who calls X, path between two nodes), `terminal`:
   ```bash
   python3 scripts/haos_wiki_query.py --edges tools.registry::dispatch
   ```
   or, when the wiki lives elsewhere, pass `--root <path-to-codebase-wiki>`.

## Quick Reference

| Need | Artifact | Example |
|---|---|---|
| Overview, god nodes, bridges | `index.md` | read front door first |
| One community's concepts + relations | `modules/<slug>.md` | `modules/agent.md` |
| Full structured graph | `graph.json` | `search_files` + query script |
| Who calls / imports a node | query script | `--edges <node_id>` |
| Shortest path between nodes | query script | `--path <a> <b>` |
| Honest provenance | every edge carries `confidence` | prefer `EXTRACTED` over `INFERRED` |

## Procedure

1. **Generate if missing**: if `~/.hermes/codebase-wiki/index.md` does not exist, run
   `hermes codebase-wiki .` in the repo root via `terminal`.
2. **Orient**: `read_file` `index.md` — god nodes tell you the hubs, "Comunidades" links the
   areas, "Pontes" the cross-area seams.
3. **Dive**: open the community article for the area the question concerns.
4. **Confirm with edges**: for "does A use B?", query the graph with the helper script and
   prefer `EXTRACTED` edges; treat `INFERRED` as a lead to verify in source.
5. **Read the source last**: once the wiki pinpoints `file:line`, switch to `read_file` on that
   file to answer semantic questions.
6. **Report honestly**: if the wiki has no node for a concept, say the map was generated from
   `.py` only and the symbol may live in non-indexed code or not exist yet.

## Pitfalls

- Do not treat `INFERRED` edges as fact — they are resolution leads, verify in source.
- Do not query `graph.json` with ad-hoc `grep`-style parsing; use the helper script which
  handles the node/edge schema.
- Do not skip the wiki when it exists: reading `index.md` + one article is cheaper and more
  precise than scanning the tree by hand.
- Do not inject the wiki into prompts: it is consulted on demand, never pasted wholesale.

## Verification

Run the query helper against the generated wiki and confirm it returns structured output:

```bash
python3 scripts/haos_wiki_query.py --gods --limit 3
```

Then confirm the front door exists and is readable:

```bash
test -f "$(python3 -c 'from hermes_constants import get_hermes_home; import pathlib; print(pathlib.Path(get_hermes_home())/"codebase-wiki"/"index.md")')" && echo OK
```
