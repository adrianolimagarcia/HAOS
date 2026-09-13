# Upstream ACP + LSP Seams — Mapa de Auditoria (Frente 3)

> Fonte: auditoria de subagente sobre o fork HERMES-TURBO (acp_adapter/ upstream,
> agent/lsp/ upstream, hermes/platform/ scaffold HAOS). Nenhum arquivo foi
> modificado durante a auditoria; todas as citações são `caminho:linha` deste tree.

## Escopo

- **K5 "ACP real"**: mapear a superfície upstream `acp_adapter/` (ciclo de vida de
  sessão, framing, handshake) para um wrapper fino stdlib-only em
  `hermes/platform/protocols/acp/` com teste de contrato.
- **K6 "LSP real"**: mapear o que `hermes/platform/capabilities/lsp/manager.py`
  (hoje mock) precisa para `get_client(workspace)` falar LSP de verdade (JSON-RPC
  2.0 sobre stdio a um language server) com teste de contrato via subprocess
  scriptado — sem language server externo.
- Restrição transversal: **stdlib-only**, zero import de runtime externo no tree HAOS.

## Achados-chave

### A1 — Upstream `acp_adapter/` (depende de dep externa; framing não é do adapter)
- 14 módulos; entradas públicas: `entry.main()` (acp_adapter/entry.py:160) →
  `HermesACPAgent()` → `asyncio.run(acp.run_agent(agent, use_unstable_protocol=True))`
  (entry.py:201); console script `hermes-acp` (pyproject.toml:394).
- `class HermesACPAgent(SlashCommandsMixin, acp.Agent)` (server.py:226); handlers
  async: `initialize` (497), `authenticate` (520), `new_session(cwd, mcp_servers=None)`
  (583), `load_session` (588), `resume_session` (598), `fork_session` (625),
  `list_sessions` (639), `cancel` (608), `prompt(prompt, session_id)` (776),
  `set_session_model/mode/config_option` (929/941/955); `on_connect(conn)` guarda o
  `acp.Client` p/ notificações `session/update` (254–266).
- Substrato: `SessionManager(agent_factory=None, db=None)` com create/get/fork/list/
  update_cwd/save_session + `@dataclass SessionState` (session.py:131–253).
- **Framing = dep PyPI `agent-client-protocol==0.9.0`** (import `acp`; extra
  opcional exato, pyproject.toml:283; só depende de pydantic, uv.lock:121).
  O adapter NÃO faz framing: o pacote `acp` usa **JSON-RPC 2.0 newline-delimited**
  (`json.dumps(payload, separators=(",",":")) + "\n"`, acp/task/sender.py:24;
  recv = readline + json.loads, acp/connection.py:151; docstring "newline-delimited",
  connection.py:62). NÃO é Content-Length (diferente do LSP).
- Nomes de método no fio (`acp/meta.py`): `initialize`, `authenticate`,
  `session/new|load|resume|fork|list|prompt|cancel|close|set_config_option|
  set_mode|set_model`; agent→client `session/update`. `PROTOCOL_VERSION = 1`.
  Params camelCase via alias (ex. `protocolVersion`, `mcpServers`).
- **Handshake mínimo**: `initialize{protocolVersion:1}` → result{agentInfo,
  agentCapabilities, authMethods} (server.py:497–518) → `session/new{cwd:"/abs"}`
  → {sessionId} → `session/prompt{sessionId, prompt:[{type:"text",text}]}`.
- Imports nus (python3 3.14.7, cwd=raiz): `import acp` →
  `ModuleNotFoundError: No module named 'acp'` (não instalada no env);
  `import acp_adapter` / `.entry` / `.session` → OK; `import acp_adapter.server` →
  falha em server.py:17 (`import acp`) — TODO módulo de server real é bloqueado
  pela dep ausente.

### A2 — Família `hermes/platform/protocols/` (estilo do scaffold)
- Só `bus.py` (EventBus async pub/sub, bus.py:7–57) e `anp/adapter.py`
  (dataclass `ANPIdentity` + `ANPAdapter` retornando dicts, anp/adapter.py:4–25).
  **Sem dir `protocols/acp/`**. Estilo: stdlib-only, sem pydantic, dataclass+dict;
  `adapters/kilo/adapter.py:5–22` idem. Novo módulo ACP deve espelhar esse estilo.

### B1 — Mock atual do LSP (scaffold)
- `capabilities/lsp/manager.py` (36 linhas): `LSPClient(workspace_path,
  language="python")` com retornos **hard-coded** em `get_document_symbols` (12),
  `find_references` (18), `get_diagnostics` (24) — o mock é manager.py:12–25;
  `LSPManager.get_client(workspace_path, language="python")` memoiza por path (33).

### B2 — Upstream JÁ TEM client LSP real (`agent/lsp/`, ~2,6k LOC)
- Facade `get_service()/shutdown_service()` (agent/lsp/__init__.py:39–72).
- `LSPService.create_from_config/enabled_for/snapshot_baseline/
  get_diagnostics_sync/shutdown/get_status` (manager.py:135–309); um `LSPClient`
  por (server_id, root); pyright multi_root → 1 processo + `didChangeWorkspaceFolders`.
- `LSPClient(server_id, workspace_root, command,...)` (client.py:127): spawn (208),
  `initialize`{rootUri, rootPath, processId, workspaceFolders, capabilities} +
  notif `initialized` (285–298), `open_file` (474), `wait_for_diagnostics` (549),
  `diagnostics_for` (616).
- **Framer stdlib puro**: `Content-Length: N\r\n\r\n<body>` —
  `encode_message`/`read_message` (protocol.py:37–99); só importa asyncio/json.
- Registro `SERVERS` ~28 servidores (pyright, typescript, gopls, rust-analyzer,
  clangd, bash-language-server…), servers.py:265–326; `find_server_for_file` (340).
- Testes upstream já têm o padrão "servidor scriptado": `tests/agent/lsp/
  _mock_lsp_server.py` (stdlib-only, env `MOCK_LSP_SCRIPT` clean/errors/crash/…)
  + `test_client_e2e.py` que sobe o mock como subprocess real.

### B3 — Viabilidade stdlib + binários
- Framing LSP é stdlib-viável (prova: agent/lsp/protocol.py). Cliente requer só
  stdlib (asyncio subprocess). **Binários LS ausentes no env**: `which pyright
  gopls pylsp basedpyright` → todos "no …"; `ls /usr/bin | grep -iE 'lsp|pyright|
  gopls'` → só `lirc-lsplugins`, `live555HLSProxy`, `lspci` (não relacionados).
  ⇒ teste de contrato PRECISA de servidor scriptado.

### B4 — Consumidores do `get_client`
- Único import e **zero call sites** em código: `haos_server.py:21` importa
  `LSPManager` sem usar. Forma futura fixada por:
  - `tasks/acceptance.py:22–26` — critério `lsp` lê `context["lsp_diagnostics"]
    .new_errors` (bate com o mock get_diagnostics → {"new_errors":0,…}).
  - `memory/context_engine/compiler.py:15,47–50` — `lsp_symbols` opcional.
  - `capabilities/registry.py:60–62` — capability `code-intelligence` ← provider
    `hermes-lsp`; `extensions/registry.py:35` — `requires: "capability:lsp"`.
  - INTEGRATIONS.md (linha K6) e COMPLIANCE.md:32 — LSP por worktree
    (`get_client(workspace_path)`).

## Lacunas acionáveis

1. **Wrapper ACP stdlib-only inexistente** — o quê: criar `protocols/acp/` com
   cliente de sessão (initialize/new_session/prompt/close) falando newline-delimited
   JSON-RPC 2.0 via subprocess; nunca `import acp`. Onde no upstream: wire contract
   em acp/meta.py + acp/task/sender.py + server.py:497–583 (handlers/schema).
   Por que importa: K5 exige handshake/sessão ACP contra a base upstream sem arrastar
   a dep `agent-client-protocol` para o tree HAOS.

2. **Dep ausente trava o server real** — o quê: `import acp_adapter.server` falha
   (server.py:17) sem `agent-client-protocol==0.9.0`; todo teste que importe o server
   upstream exige o extra `[acp]`. Onde: pyproject.toml:283. Por que: teste de
   contrato K5 deve ser verde stdlib-only; o teste contra o processo real vira
   skip-if-sem-dep, nunca requisito de CI.

3. **Mock LSP hard-coded** — o quê: trocar retornos literais (manager.py:12–25) por
   cliente real que spawna servidor; manter API `LSPClient.workspace_path/language/
   running` + `get_document_symbols/find_references/get_diagnostics` para não quebrar
   acceptance.py:24 e a forma documentada (K6 "mock vira stub explícito"). Onde:
   agent/lsp/client.py:127–298 (ciclo spawn/initialize/didOpen) + framer
   protocol.py:37–99 para copiar. Por que: `get_client(workspace)` hoje devolve dados
   falsos; critério de aceitação `lsp` (acceptance.py:24) e o ContextBuilder
   (compiler.py:47) consumiriam diag/símbolos inventados.

4. **Sem key por linguagem no LSPManager** — o quê: `get_client` cacheia por path
   (manager.py:33–36); um segundo workspace multi-linguagem precisaria de key
   (path, language). Onde: manager.py:30–36; padrão multi-key upstream
   agent/lsp/manager.py:26–33. Por que: worktrees híbridos exigiriam um servidor por
   linguagem; upstream resolve por extensão via `find_server_for_file` (servers.py:340).

5. **Sem binários LS no env** — o quê: CI não tem pyright/gopls. Onde: verificação
   B3. Por que: o teste de contrato K6 deve subir um servidor scriptado
   (padrão tests/agent/lsp/_mock_lsp_server.py) que fale Content-Length framing —
   é a única integração executável sem dependência externa.

## Status de implementação

- Nenhum achado acima virou código/teste no tree **nesta auditoria** (relatório
  somente). Árvore de destino já existente: `tests/platform/` (ex.
  tests/platform/observability, test_full_platform.py) é o lar dos testes de
  contrato; `docs/haos/audits/` registra este relatório (índice em
  docs/haos/audits/README.md, linha "ACP + LSP upstream seams").
- Pré-existente no upstream (reutilizável, não é do HAOS): client LSP completo em
  agent/lsp/ e o mock LSP scriptado em tests/agent/lsp/_mock_lsp_server.py.

## Recomendações

- **K5**: espelhar o estilo da família (`protocols/anp/adapter.py`): dataclass de
  sessão + classe cliente cujo ctor recebe `command: List[str]`; implementar só
  initialize/new_session/prompt/close sobre newline JSON-RPC (method names
  `initialize`/`session/new`/`session/prompt`, aliases camelCase). Teste de
  contrato: peer scriptado stdio que responde envelopes ACP; asserir
  `protocolVersion == 1`, `agentInfo.name`, round-trip de sessionId; segundo teste
  skip-if-`import acp`-falha spawna `python -m acp_adapter` real.
- **K6**: manter API pública do manager; cliente real copia o framer
  Content-Length (agent/lsp/protocol.py:37–99) e o ciclo spawn→initialize→
  `initialized`→didOpen (client.py:192–298); `get_diagnostics()` alimenta
  `new_errors` de acceptance.py:24 a partir de publishDiagnostics. Teste de
  contrato: subir mock scriptado (padrão upstream) como subprocess e asserir
  initialize + documentSymbol/references + publishDiagnostics + shutdown/exit.
- Ambos: testes em `tests/platform/protocols/test_acp.py` e
  `tests/platform/capabilities/test_lsp.py`, rodando por `scripts/run_tests.sh`
  (HERMES_HOME temporário, isolamento por arquivo) — sem import de runtime externo.
