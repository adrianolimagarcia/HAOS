# Hermes CLI Reference

Live sources when anything looks stale: `haos --help`, `haos <command> --help`,
https://hermes-agent.nousresearch.com/docs/reference/cli-commands

### Global Flags

```
haos [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
haos chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
haos setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
haos model                Interactive model/provider picker
haos fallback [add|remove|list]  Fallback provider chain
haos config [show|edit|get|set|unset|path|env-path|check|migrate]
haos login / logout       OAuth sign-in / clear stored auth
haos doctor [--fix]       Check dependencies and config
haos status [--all]       Component status
```

### Tools & Skills

```
haos tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

haos skills list|browse|search QUERY|inspect ID
haos skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
haos skills config        Enable/disable skills per platform
haos skills check|update|uninstall|publish PATH
haos skills tap add REPO  Add a GitHub repo as a skill source
haos bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
haos mcp add NAME (--url or --command) | remove | list | test NAME
haos mcp catalog | install NAME     Curated catalog install
haos mcp configure NAME             Toggle tool selection
haos mcp serve                      Run Hermes as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
haos gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `haos photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/

### Sessions

```
haos sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
haos cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
haos webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
haos profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
haos profile rename A B | alias NAME | export NAME | import FILE
haos profile migrate-identity A B   Retry a completed rename's session/routing identity migration
```

### Credentials & Pools

```
haos auth                 Interactive credential manager
haos auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
haos auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
haos desktop / gui        Native desktop app
haos dashboard            Web admin panel + embedded chat (--stop / --status)
haos proxy                OpenAI-compatible local proxy backed by an OAuth provider
haos portal               Quick setup / sign in via Nous Portal
haos kanban <verb>        Multi-agent work-queue board
haos project              Named multi-folder workspaces
haos skin list|use|set    Switch/tweak skins (see references/themes.md)
haos pets <verb>          Pet mascots (see references/petdex.md)
haos memory setup|status|off|reset   Memory provider
haos secrets bitwarden|onepassword   External secret stores
haos moa                  Mixture-of-Agents slots
haos hooks / security / backup / import / checkpoints / console
haos logs [-f] [errors]   View agent/error logs
haos send                 One-off message through a gateway platform
haos pairing / plugins / insights / journey / computer-use
haos acp                  ACP server (IDE integration)
haos completion bash|zsh|fish
haos update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `haos photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `haos config edit` · [Configuration docs](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) |
| Tools / toolsets | `haos tools list` · [Tools reference](https://hermes-agent.nousresearch.com/docs/reference/tools-reference) |
| Skills catalog | `haos skills browse` · [Skills catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) |
| Provider setup | `haos model` · [Providers guide](https://hermes-agent.nousresearch.com/docs/integrations/providers) |
| Env variables | `haos config env-path` · [Env vars reference](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) |
| Gateway logs | `~/.hermes/logs/gateway.log` (or `haos logs`) |
| Sessions | `haos sessions browse` (reads state.db) |
