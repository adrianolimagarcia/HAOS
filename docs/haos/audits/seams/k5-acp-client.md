# Auditoria de seam: K5 — cliente ACP wire (handshake/sessão)

## 1. Escopo
- Card Fase-0 **K5 "ACP real"** do `hermes/platform/INTEGRATIONS.md` (§2): módulo HAOS sob
  `hermes/platform/protocols/acp/` que fala o *wire* ACP (Agent Client Protocol) para um
  IDE/host abrir sessão contra um agente ACP.
- **Aceite:** "Handshake/sessão ACP contra base upstream em teste de contrato".
- **Restrições do card:** stdlib-only; **nunca** importar `acp` (agent-client-protocol==0.9.0)
  nem `acp_adapter.server` (não instalados neste tree); wire implementado do zero
  (JSON-RPC 2.0 NEWLINE-delimited via `subprocess`); peer scriptado nos testes espelha as
  shapes do wire; sem `__init__.py` (PEP-420); não tocar upstream (`acp_adapter/`, `tools/`,
  `agent/`, `hermes_cli/`) nem outros módulos HAOS.

## 2. O que foi implementado (arquivos/caminhos exatos no tree)
1. **`hermes/platform/protocols/acp/adapter.py`** — cliente do wire ACP (único arquivo do módulo).
   - `PROTOCOL_VERSION = 1`; erros `ACPError(RuntimeError)` → `ACPProtocolError` /
     `ACPUnavailableError`.
   - `ACPIdentity` (name/version/protocol_version/agent_capabilities/auth_methods);
     `ACPSession` (session_id/cwd/client + `to_dict()`).
   - `ACPSessionClient`: `start()` (spawn + handshake `initialize`; armazena `ACPIdentity`;
     `protocolVersion` divergente → `ACPProtocolError`; processo morre antes → 
     `ACPUnavailableError`; falha no handshake derruba o processo — sem vazamento),
     `new_session(cwd)` (`session/new`), `send_text(session, text)` (`session/prompt`,
     retorna o resultado **cru**, sem sobre-parsing), `close()` (`session/close` por sessão
     conhecida → EOF no stdin → `wait` com timeout → `terminate`/`kill`), suporte a
     context-manager; props `is_running`/`returncode`/`notifications`/`stderr_tail`.
   - Transporte: frame = `json.dumps(payload, separators=(",", ":")) + "\n"`; thread reader
     correlaciona respostas **por id** inteiro crescente, grava pushes do agente
     (`session/update`, `fs/*`, `terminal/*`) e nunca as responde; timeouts em todo request
     (`request_timeout_s`, default 15 s); stderr drenado em thread (diagnóstico de falha).
2. **`tests/platform/protocols/_mock_acp_server.py`** — peer ACP scriptado (stdlib, stdio):
   `initialize` → `protocolVersion` 1 / `agentInfo {name:"mock-acp-agent", version:"0.0.1"}`;
   `session/new` → `{sessionId:"sess-mock-1", models:[], modes:[]}`; `session/prompt` →
   notificação `session/update` **antes** do result `{"result":[{"type":"text","text":"ok"}]}`
   (exercita o caminho de pushes interleaved); `session/close` → null; sai limpo no EOF do
   stdin. Grava cada request recebido como uma linha JSON em `MOCK_ACP_RECORD`;
   `MOCK_ACP_PROTOCOL_VERSION` força versão (teste de mismatch).
3. **`tests/platform/protocols/test_acp.py`** — suíte de contrato `unittest` (hermética).

## 3. Achados-chave
- O wire ACP real é JSON-RPC 2.0 **NEWLINE-delimited sobre stdio** (não Content-Length) —
  confirmado contra agent-client-protocol==0.9.0 / `acp_adapter`; os testes provam o contrato
  sem importar nenhum dos dois.
- Pushes do agente chegam interleaved com respostas; correlação estrita por `id` + gravação
  das notificações (nunca respondidas) evita deadlock no reader.
- Todo request é bounded por timeout; `close()` bounded (`shutdown_timeout_s`, default 5 s)
  com escalada terminate→kill; `start()` com falha derruba o processo.
- Threads reader/stderr são daemon, unidas com timeout e streams fechados explicitamente →
  suíte roda limpa até com `-W error::ResourceWarning`.
- Params do wire usam **camelCase** (`protocolVersion`, `clientCapabilities`, `clientInfo`;
  `session/new` → `{"cwd": <absoluto>}`) — travado por teste de recorder.

## 4. Lacunas acionáveis restantes
1. **E2E opcional contra o `acp_adapter` real** (spawn) — adiar até `import acp` funcionar em
   venv isolado (aqui não instalado). Não feito de propósito (instrução do card).
2. **Requisições agent→client com id** (ex.: `fs/read_text_file` como *request*, não push) são
   hoje gravadas e **nunca respondidas**; um agente real pode bloquear. Decidir política
   (responder erro `-32601` vs ignorar) quando houver E2E real.
3. **Sem parser de resultado** de `session/prompt` (captura crua); decisão de consumer futuro
   (`agentMessage`/updates).
4. **Prompts longos** (agente real demorando) exigem `request_timeout_s` maior ou streaming via
   notificações; o knob já existe, streaming não.
5. **API síncrona 1-thread** (1 request in flight) — sem pipelining; multi-sessão suportada
   internamente (`_sessions`), mas sem concorrência.

## 5. Status de testes
- **Suíte:** `tests/platform/protocols/test_acp.py` (unittest; TemporaryDirectory para cwd +
  arquivos de record; nunca toca `~/.hermes`; tudo com timeout para hang falhar, não travar).
- **Resultado:** `Ran 9 tests … OK` — **verde**, estável em execuções repetidas e com
  `-W error::ResourceWarning`.
  Comando: `PYTHONPATH=. python3 tests/platform/protocols/test_acp.py 2>&1 | grep -vE "^Failed to load plugin"`.
- **Import check:** `PYTHONPATH=. python3 -c "import hermes.platform.protocols.acp.adapter as m;
  print('ok', hasattr(m, 'ACPSessionClient'))"` → `ok True`.
- **Cobertura dos 9:** handshake → `ACPIdentity` (name `mock-acp-agent`, protocol_version 1);
  `new_session` → `sess-mock-1`; `send_text` → result cru + push `session/update` gravada;
  `close()` encerra o processo (rc 0, idempotente); recorder camelCase do wire (initialize
  params `protocolVersion`/`clientCapabilities`/`clientInfo`; `session/new` params exatos
  `{"cwd": <absoluto>}`); negativo exit-imediato → `ACPUnavailableError`; mismatch de versão →
  `ACPProtocolError`; timeout de `start()` → `ACPError` com cleanup; context-manager E2E.

## 6. Recomendações
- Marcar a linha **K5** do `hermes/platform/INTEGRATIONS.md` como ✅ (e atualizar
  `COMPLIANCE.md`/`REFERENCES.md`) quando a Fase 0 consolidar.
- Adicionar o E2E opcional contra o `acp_adapter` real assim que `import acp` estiver
  disponível em venv isolado — fora do processo do agente (política do card).
- Definir política para requisições agent→client antes de uso contra agente real.
- Reaproveitar `ACPSessionClient` como cliente genérico para a UI ACP (Fase 3 do mapa): a
  camada de sessão (`sessionId`/`cwd`) já isola o wire do resto do HAOS.
