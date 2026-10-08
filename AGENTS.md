# Hermes Agent - Development Guide

Instructions for AI coding assistants and developers working on the hermes-agent codebase.
This root file holds only what applies everywhere. Each area has its own `AGENTS.md` (aim for
~8k chars; `agent/subdirectory_hints.py` delivers up to 32k and truncates head/tail with a warning
past that); see the **routing table** at the end and read the area file before editing in that area.

**Never give up on the right solution.**

> **HAOS fork — leia primeiro:** este repo é o appliance HAOS. Antes de
> trabalhar no appliance/VM/distro, leia `docs/haos/DEV_WORKFLOW_VM.md` (política
> DEV na VM, caminhos, pipeline VM→ISO, invariantes de deploy). Builds de imagem
> vivem em `distro/haos-linux/`.
>
> **Todo commit no `main` incrementa +1 na versão** (`0.21.4` → `0.21.5`), no
> mesmo commit. Rode `python scripts/release.py --bump patch --bump-only` — ele
> escreve os arquivos de versão e para, sem commitar, taguear ou publicar.
>
> O patch é um **contador monotônico deliberado, não semver**: `0.21.9` →
> `0.21.10` → `0.21.11`, indefinidamente, sem reset e sem significar
> compatibilidade. Isso é intencional (dono, 19/09/2026) — não "conserte" para
> semver nem resete o patch ao mexer no minor.
>
> A versão vive em `hermes_cli/__init__.py` (`__version__` e `__release_date__`),
> `pyproject.toml`, `apps/desktop/package.json` e nos arquivos do
> bootstrap-installer. Num install editable, rode `pip install -e . --no-deps`
> depois, senão o metadado fica para trás — o agente lê a própria versão por
> `importlib.metadata` em quatro caminhos. Detalhes e armadilhas:
> `docs/haos/DEV_WORKFLOW_VM.md` §5 item 6.

## What Hermes Is

Hermes is a personal AI agent running the same agent core across CLI, messaging
gateway (Telegram, Discord, Slack, ~20 platforms), TUI, and Electron desktop app.
Extended primarily through **plugins and skills**, not by growing the core.

Two sacred invariants shape almost every design decision:
- **Per-conversation prompt caching is sacred:** Reuses a cached prefix every turn. Mutating past context, swapping toolsets, reloading memories, or rebuilding system prompts mid-conversation invalidates cache and multiplies cost (exception: context compression). Slash commands mutating system-prompt state must be **cache-aware**: default to deferred invalidation (next session) with opt-in `--now` (`/skills install --now`).
- **The core is a narrow waist; capability lives at the edges:** Model tools are sent on every API call. New capability arrives as CLI command + skill, service-gated tool (`check_fn`), or plugin — not core surface.

## Contribution Rubric & Intent

The core agent and tool schema is a narrow waist: additions are paid for on every API call.
Expansive at the edges, conservative at the waist.

### What We Want
- **Fix real bugs, well:** Reproduce on current `main`, point to exact line, fix whole bug class across siblings.
- **Expand reach at the edges:** New adapters, channels, providers, models, desktop/TUI/dashboard features land routinely via existing setup UX (`hermes tools`, `hermes setup`, auto-install) rather than raw env vars.
- **Refactor god-files into clean modules:** Mechanical extraction PRs are welcome work.
- **Keep core narrow:** Ladder: extend existing code → CLI command + skill → service-gated tool (`check_fn`) → plugin → MCP server in catalog → core tool (last resort).
- **Extend, don't duplicate:** Check existing infra first. Design ABC + orchestrator when 3+ PRs integrate same category.
- **Behavior contracts over snapshots:** Assert relationships between data, never freeze arbitrary current values.
- **E2E validation:** Real imports against temp `HERMES_HOME` (A→B→A under multiplex for profile scope).
- **Invariants:** Preserve prompt caching, strict role alternation, and byte-stable system prompts.

### What We Don't Want
- **Speculative infrastructure:** Hooks/callbacks without concrete consumers.
- **New `HERMES_*` env vars for non-secret config:** `.env` is for secrets only; behavioral settings go in `config.yaml`.
- **New core tool when terminal + file (or skill) works:** Fix the environment or mounts instead.
- **Lazy-reading escape hatches on instructional tools:** No pagination on tools models read fully (skills, prompts).
- **Fixes that destroy features they secure:** Verify original intent (`git log -p -S`) before restricting behavior.
- **Outbound telemetry without opt-in gating:** No analytics or tracking without user config gate.
- **Third-party products in-tree:** Vendor SaaS connectors and observability backends belong in standalone plugin repos (`~/.hermes/plugins/`).

### Verifying Bug Premise Before Closing
- **Intentional design, not a gap:** Profiles are independent islands; check intent before assuming an omission.
- **Premise doesn't hold against runtime:** Point to exact manifesting line and verify fix alters it.
- **Load-bearing absence:** Deliberate omission prevents shadowing/conflicts.
- **Overreach / resurrected dead approaches:** Scope creep or reviving closed directions is rejected.

### The Footprint Ladder (New Capability Decision)
1. **Extend existing code** — zero new surface.
2. **CLI command + skill** — config/state/infra expressible as shell commands (`hermes cron`, `hermes tools`).
3. **Service-gated tool (`check_fn`)** — structured params/returns needed AND prerequisite configured.
4. **Plugin** — lives in `~/.hermes/plugins/` or pip package.
5. **MCP server (in catalog)** — reached via built-in MCP client, zero core schema footprint.
6. **New core tool** — only when fundamental, broadly useful, and unreachable via terminal/file/MCP.

### Surface capability is a property of the SESSION
- Tools depending on client type (desktop panes, in-app browser) resolve availability from session metadata/platform (`desktop_ui`, `project`), never backend process env vars (`HERMES_DESKTOP=1` means spawned by app, not that a GUI is watching).

## Development Environment

```bash
source .venv/bin/activate   # or: source venv/bin/activate
```
`scripts/run_tests.sh` probes `.venv`, then `venv`, then `$HOME/.hermes/hermes-agent/venv`
(worktrees sharing the main checkout's venv).

## Project Structure

Counts shift constantly; the filesystem is canonical. Load-bearing entry points:

```
hermes-agent/
├── run_agent.py          # AIAgent facade; the turn loop lives in agent/turn_*.py
├── model_tools.py        # Tool orchestration, discover_builtin_tools(), handle_function_call()
├── toolsets.py           # TOOLSETS dict, _HERMES_CORE_TOOLS
├── cli.py                # HermesCLI (REPL, slash dispatch) + hermes_cli/cli_*_mixin.py
├── hermes_state.py       # SessionDB facade; hermes_state_*.py siblings
├── hermes_constants.py   # get_hermes_home(), display_hermes_home() — profile-aware paths
├── hermes_logging.py     # agent.log / errors.log / gateway.log (profile-aware)
├── batch_runner.py       # Parallel batch processing
├── agent/                # turn_*.py loop phases, providers, memory, compression, prompt builder
├── hermes_cli/           # CLI subcommands, setup, config, plugins loader, skins, updater
│   └── web_routers/      # Dashboard FastAPI routers (one per surface); web_server.py mounts them
├── tools/                # Tool implementations, auto-discovered via tools/registry.py
│   └── environments/     # Terminal backends (local, docker, ssh, modal, daytona, singularity)
├── gateway/              # run.py facade + run_*.py phases + session*.py + platforms/
│   ├── platforms/        # One adapter per platform; see platforms/ADDING_A_PLATFORM.md
│   └── builtin_hooks/    # Always-registered gateway hooks (extension point; none shipped)
├── plugins/              # memory/, context_engine/, model-providers/, kanban/, image_gen/, ...
├── skills/               # Built-in skills (by category)   optional-skills/: shipped, not active
├── ui-tui/               # Ink (React) terminal UI — `hermes --tui`
├── tui_gateway/          # Python JSON-RPC backend for TUI + Desktop — server.py + methods_*.py
├── apps/desktop/         # Electron desktop app (+ apps/shared JSON-RPC client)   web/: dashboard SPA
├── acp_adapter/          # ACP server (VS Code / Zed / JetBrains)
├── cron/                 # jobs.py + scheduler.py (+ scheduler_*.py)
├── evals/                # Offline benchmarks (codebase_navigability/, compaction/, ...)
├── scripts/              # run_tests.sh, release.py, check_compat_pointers.py, ci/
├── website/              # Docusaurus docs (developer-guide/ holds the long-form area docs)
└── tests/                # Pytest suite (~39k tests / ~3.7k files, Sep 2026)
```

**User state:** `~/.hermes/config.yaml` (settings), `~/.hermes/.env` (secrets only),
`~/.hermes/logs/` (`agent.log` INFO+, `errors.log` WARNING+, `gateway.log`); all
profile-aware via `get_hermes_home()`. Browse logs with `hermes logs [--follow] [--level] [--session]`.

**Dependency chain:** `tools/registry.py` (no deps) ← `tools/*.py` (register at import) ←
`model_tools.py` (discovery) ← `run_agent.py`, `cli.py`, `batch_runner.py`, `environments/`.

### Facade + siblings layout (Sep 2026 decomposition)

Every former god file is a **facade** (public entry points + the names other packages import)
plus **siblings** `<stem>_<topic>.py` in the same directory, each owning one topic. Largest
families: `hermes_state.py` (21), `gateway/run.py` (15), `tools/mcp_tool.py` (15),
`hermes_cli/kanban.py` (14), `hermes_cli/web_server.py` (13 + 24 routers), `hermes_cli/auth.py`
(12), `tools/browser_tool.py` (11), `cli.py` (12 `hermes_cli/cli_*_mixin.py`), `run_agent.py`
(`agent/turn_*.py`, `agent_init.py`, conversation_loop.py).

- **Find code by topic, not by facade:** `grep -rn "def name" <dir>/<stem>_*.py`.
- **Siblings may import each other and late-import the facade** inside functions. Facade never imports a sibling at module level *and* gets imported by that sibling at module level.
- **Patch where production reads:** Siblings often do `from <facade> import name` inside functions so `monkeypatch.setattr(facade, "name", ...)` is the seam.
- **Compat pointers are OFF LIMITS in-tree:** Old import paths for external plugins (`scripts/check_compat_pointers.py`) must not be used in-tree.
- **Don't recreate god files:** File > 2k lines or function > 300 lines / CC 30 triggers splitting along `<stem>_<topic>`.
- **No `if/elif` ladders ≥ 4 branches keyed on a name/kind** — use a dict/table → handler.
- **No re-export shims for internal moves:** Internal paths are not API.
- **Moving a symbol means fixing its docs in the same PR:** grep `website/docs`, `skills/`, and area `AGENTS.md`.

## Code Shape Rules (all languages)

- No "defense-in-depth" wrappers, `try/except: pass` around code that cannot fail, or flags nobody sets. Docstrings/comments keep the WHY, cut the WHAT.
- **Never infer process identity from argv substrings** (`"serve" in cmdline`) — canonical matchers: `gateway.status.looks_like_gateway_command_line` and `hermes_cli.update_cmd._hermes_holder_subcommand`; derive flags from parser (`_holder_value_flags()`); match full cmdlines. Details: `hermes_cli/AGENTS.md` and `gateway/AGENTS.md`.
- **Never hardcode `~/.hermes`.** `get_hermes_home()` for code paths, `display_hermes_home()` for user-facing text (from `hermes_constants`). Profile operations are HOME-anchored (`_get_profiles_root()` = `Path.home()/.hermes/profiles`).
- **One process may serve many profiles; code that runs outside a turn binds the owning profile scope explicitly.** A profile = home + secret scope + terminal scope, bound by `gateway/run.py::_profile_runtime_scope` (turn), `tui_gateway/server.py::@_profile_scoped` + `model_switch.py::_session_profile_runtime_scope` (RPC, teardown), `cron/scheduler_provider.py::_profile_cron_scope` (ticker), `gateway/run_agent_cache.py::_run_release_in_profile_scope` (eviction). An unbound read leaks default profile; key slots by `hermes_home_key()` or resolve at call time. Advisory lint: `scripts/check_profile_scope_patterns.py`.
- **Argparse alias dispatch:** `add_parser("list", aliases=["ls"])` sets `dest` to literal typed (`"ls"`). Dispatch must accept both (see `hermes_cli/AGENTS.md`).
- **Don't wire in dead code without E2E validation.** Exercise real resolution chain with real imports against temp `HERMES_HOME`.
- **TypeScript style:** See `apps/desktop/AGENTS.md` § TypeScript Style Rules for desktop/frontend conventions (nanostores, thin routes, interface over type).

## Dependency Pinning Policy

All dependencies carry upper bounds (litellm compromise #2796/#2810; Mini Shai-Hulud worm,
May 2026). PyPI: `>=floor,<next_major` (`"httpx>=0.28.1,<1"`); pre-1.0: `<0.(minor+2)`
(`>=0.29,<0.32`). Git URLs: 40-char commit SHA. GitHub Actions: SHA + `# vN` comment. CI-only
pip: `==exact`. A bare `>=X.Y.Z` is rejected by CI and reviewers. Run `uv lock` after
changing `pyproject.toml`. Reference: #2810 (bounds), #9801 (SHA pinning + audit CI).

## Commits, Merges, PRs

- **Squash merges from stale branches silently revert recent fixes.** Before squash-merging,
  bring the branch to `main` (`git fetch origin main && git reset --hard origin/main`, re-apply
  the PR's commits). Verify with `git diff HEAD~1..HEAD` after merging — unexpected deletions
  are a red flag.
- Salvage by cherry-pick so contributor authorship survives (see rubric).
- Tests per fix: 1–2 INVARIANT tests (behaviour contract, proven red on base), never
  change-detectors; ≤ 2 tests is the salvage bar too. Reject/rewrite in salvaged diffs:
  appendages to facades, new god helpers, compat aliases, wrappers.

## Testing (applies everywhere)

**ALWAYS use `scripts/run_tests.sh`**, never bare `pytest`. It enforces CI parity: credential vars unset, `TZ=UTC`, `LANG=C.UTF-8`, `HERMES_HOME` → temp dir, and per-file subprocess isolation via `scripts/run_tests_parallel.py` (no xdist; workers scale with CPU count).

```bash
scripts/run_tests.sh                                    # full suite
scripts/run_tests.sh tests/gateway/                     # one directory
scripts/run_tests.sh tests/agent/test_foo.py -k test_x  # file-granular; -k narrows
scripts/run_tests.sh -v --tb=long                       # pytest flags pass through
```

- **Flake policy:** Failing file retried once in fresh subprocess (`--file-retries`). Pass-on-retry is printed under `⚠ FLAKY` — a bug to fix. Timing tests must use event-based sync or generous bounds (see `website/docs/developer-guide/contributing.md`).
- **Placement mirrors source tree:** Tests live in `tests/<top-level source dir>/` (`tests/hermes_cli/`, `tests/agent/`, `tests/gateway/`). Root-level modules sit in `tests/`. No issue numbers in filenames (cite in docstring).
- **Placement (CI lanes):** JS/TS-specific tests belong in vitest suite, not `tests/*.py` (classified by `scripts/ci/classify_changes.py`).
- **Tests must not write to `~/.hermes/`:** `_isolate_hermes_home` redirects `HERMES_HOME`. Profile tests mock `Path.home()` and set `HERMES_HOME`.

### Don't fake the host OS
- Differing behavior is tested on that host with `@pytest.mark.linux_only` / `macos_only` / `windows_only`, never by patching `sys.platform`.
- Use the marker, never bare `skipif(sys.platform != ...)` (breaks CI marker grepping). Live Windows process-topology tests live in the `wine2e` lane (see `website/docs/developer-guide/contributing.md`).

### Don't write change-detector tests
- Never freeze data expected to change (catalogs, version numbers, enumeration counts). Assert contracts and relationships between data, not snapshots.

### Never read source code in tests
- Testing `.py`/`.ts` file text tests shape, not behavior — banned. Extract logic into pure/DI-testable functions and call them.

## Routing Table — working in X → read X/AGENTS.md

| Area | Read | Covers |
|---|---|---|
| `run_agent.py`, `agent/` | `agent/AGENTS.md` | AIAgent + mixins, turn phases, caching integrity, message-flow invariants, compression, model/aux resolution |
| `cli.py`, `hermes_cli/`, `main.py` | `hermes_cli/AGENTS.md` | CLI mixins, `_SLASH_DISPATCH`, slash registry, config system + loaders, skins, `hermes update` pipeline, profiles / multiplex |
| `gateway/` | `gateway/AGENTS.md` | Adapters, two message guards, streaming contract, background notifications, gateway vs desktop lifecycle, token locks, scoped secrets |
| `tools/`, `toolsets.py`, `model_tools.py` | `tools/AGENTS.md` | Adding tools, registry, toolsets, delegation, cross-tool references, backends |
| `plugins/`, `hermes_cli/plugins*.py` | `plugins/AGENTS.md` | Plugin kinds, native compat contract, in-tree policy, Sep-2026 compat window |
| `tui_gateway/`, `ui-tui/` | `tui_gateway/AGENTS.md` | Process model, JSON-RPC transport, key surfaces, slash flow, dev commands |
| `web/`, `hermes_cli/web_routers/` | `web/AGENTS.md` | Dashboard embeds the real TUI; what React may and may not rebuild |
| `apps/desktop/` | `apps/desktop/AGENTS.md`, `apps/desktop/src/AGENTS.md` | Desktop judgment guide; `serve` backend, slash palette curation, Bot Mode canonical chat |
| `skills/`, `optional-skills/`, `agent/curator*.py` | `skills/AGENTS.md` | Frontmatter, HARDLINE authoring standards, curator |
| `cron/`, kanban (`hermes_cli/kanban*.py`, `tools/kanban_tools.py`, `plugins/kanban/`) | `cron/AGENTS.md` | Scheduler invariants, job fields, kanban board/dispatcher |
| `gateway/platforms/` new adapter | `gateway/platforms/ADDING_A_PLATFORM.md` | Step-by-step adapter guide |
| profiles / multiplex / secret scope (any area) | `gateway/AGENTS.md` § Profile scope, `website/docs/user-guide/multi-profile-gateways.md` § What is isolated per profile | which execution points bind scope, what is isolated per profile |

Long-form background lives in `website/docs/developer-guide/` (agent-loop, prompt-assembly,
context-compression-and-caching, gateway-internals, tools-runtime, plugins/, cron-internals,
session-storage, ...). Workflow rules (PR/issue/review/salvage process) live in the
`hermes-agent-dev` skill, not here.
