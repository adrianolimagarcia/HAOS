# Seam: K6 — Real LSP client (HAOS platform capability)

Frente 3 — consolidação da auditoria do subagente K6. Carta original: substituir o
mock de LSP por um cliente real que fala o wire protocol, mantendo compatibilidade
de API pública com os consumidores existentes (suíte `tests/platform`).

## Escopo

- Módulo-alvo: `hermes/platform/capabilities/lsp/manager.py` (capability HAOS).
- Objetivo: `LSPManager.get_client(workspace)` deve devolver um cliente que fala
  **LSP real** (subprocesso language-server + JSON-RPC 2.0 com framing
  `Content-Length` sobre stdio); o mock de literais vira **stub explícito**
  (retornado quando nenhum servidor é resolvível), sem quebrar a API pública.
- Restrições respeitadas: apenas `hermes/platform/` e `tests/platform/` tocados;
  nada de upstream (nem `agent/lsp/protocol.py`, que foi usado só como referência
  de leitura); stdlib-only; sem `__init__.py` novos (namespace PEP-420 é
  load-bearing); comentários em inglês; sem dependências novas.

## O que foi implementado

Arquivos exatos no tree (todos novos/reescritos nesta carta):

| Caminho | Mudança |
|---|---|
| `hermes/platform/capabilities/lsp/protocol.py` | **novo** — framer LSP 3.x stdlib: `encode_message`, `read_message` (async, EOF limpo → `None`, limites de header/body), `make_request`, `make_notification` (omite `params=None`), `classify_message` → `(kind, key)`, `LSPProtocolError`. |
| `hermes/platform/capabilities/lsp/manager.py` | **reescrito** — `LSPError`/`LSPUnavailableError`; `LSPClient` real (spawn do `server_command` via `create_subprocess_exec` em loop asyncio num thread daemon; handshake `initialize`/`initialized`; `running=True` só pós-handshake; `shutdown`/`exit` com janela de saída limpa; context manager; didOpen por arquivo; reader roteia respostas por id e acumula `publishDiagnostics` por uri); `StaticLSPClient` (stub explícito, `is_stub=True`, não fala LSP, contrato legado); `LSPManager` memoizado por `workspace_path`. |
| `tests/platform/capabilities/_mock_lsp_server.py` | **novo** — servidor LSP scriptado, subprocesso real (`[sys.executable, ...]`), stdlib: initialize/shutdown/exit, didOpen→push de diagnostics (1 erro + 1 warning), documentSymbol (classe → kind 5), references. |
| `tests/platform/capabilities/test_lsp.py` | **novo** — suíte hermética unittest (TemporaryDirectory). |

## Achados-chave

- **API preservada**: `get_document_symbols` → `[{name, kind, line}]`,
  `find_references` → `[{file, line}]`, `get_diagnostics` →
  `{new_errors, warnings, details}`; normaliza ambos shapes LSP (DocumentSymbol[]
  hierárquico com flatten de `children`, e SymbolInformation[] plano) e
  Location/LocationLink. `haos_server.py` só importa `LSPManager` — intacto.
- **Fail-fast**: `server_command=None` no cliente real levanta
  `LSPUnavailableError` no start/primeiro método (nunca spawna vazio).
- **Stub não se passa por real**: `StaticLSPClient.is_stub=True` + docstring
  explícita; subclasse de `LSPClient` mas **não** chama o `__init__` do pai.
- **Resolução**: `server_command` explícito vence; senão
  `HAOS_LSP_SERVER_<LANG>` (opt-in, env); senão stub. `get_client` **não**
  auto-starta o cliente real (spawn lazy no primeiro método / `start()`).
- **Memoização legada**: `LSPManager` chaveia só por `workspace_path`
  (first-wins), paridade com o manager original.
- **Subprocesso real**: o mock roda como processo à parte e é exercitado o
  framing `Content-Length` de verdade (não in-process, não monkeypatch).

## Lacunas acionáveis restantes

1. **Pull diagnostics**: `get_diagnostics` consome só push
   (`textDocument/publishDiagnostics`). Para servidores que anunciam
   `diagnosticProvider` pull-only, caberia um `textDocument/diagnostic` pull —
   hoje há janela de settle de 3s e retorno com zeros se nada chegar.
2. **`find_references` sem arquivo aberto** levanta `LSPError`; se um consumidor
   esperar lista vazia nesse caso, é ajuste de contrato de 1 linha.
3. **Multi-arquivo**: símbolos/diagnostics são por arquivo didOpen; não há
   workspace-wide symbol search (fora da carta).
4. **Variantes de servidor**: validado contra mock; pyright/gopls reais não
   foram exercitados neste ambiente (resolução via `HAOS_LSP_SERVER_*` existe
   mas não foi testada com binário real).
5. **Timeout de handshake 15s + janela de shutdown** são conservadores; se o
   K8 (stubs de seam) expuser o cliente em caminho quente, revisar valores.

## Status de testes

- Suíte: `tests/platform/capabilities/test_lsp.py` — **15 testes, verde**:
  framer (framing/classify), handshake/running, symbols via wire, references,
  diagnostics com push capturado (contagens exatas), shutdown reap do processo,
  context manager, fail-fast, FileNotFoundError, roteamento/memoização do
  manager (stub × real, lazy).
- Legado: `tests/platform/models/test_models_capabilities.py` — **8 testes,
  verde** (inclui `test_lsp_manager`, que cai no caminho stub).
- Import check: `python3 -c "import hermes.platform.capabilities.lsp.manager;
  import hermes.platform.capabilities.lsp.protocol"` → ok. `py_compile` limpo.
- Rodar: `PYTHONPATH=. python3 tests/platform/capabilities/test_lsp.py` (e o
  arquivo models acima), filtrado de `^Failed to load plugin`.
- Observação de ambiente: o mock usa `os.read`/`os.write` crus nos fds — leitura
  bufferizada (`stdin.buffer`) do filho estagnava neste ambiente até EOF
  (verificado por sonda direta); I/O cru é determinístico.

## Recomendações

1. Integrar `get_document_symbols`/`get_diagnostics` a um fluxo E2E da platform
   (ex.: abrir arquivo real → rodar diag → alimentar `lsp_diagnostics` do
   acceptance.py) para provar o caminho quente com servidor real.
2. Adicionar teste de pull diagnostics quando um servidor pull-only existir no
   ecossistema; hoje o contrato de push está coberto.
3. Decidir contrato de `find_references` sem arquivo aberto (erro vs `[]`) e
   documentar na docstring do manager.
4. Manter o `StaticLSPClient` como fallback default: não auto-spawnar servidores
   assumidos instalados (pyright/gopls) sem opt-in explícito.
5. Quando K7/K8 fecharem os stubs de seam, reaproveitar `protocol.py` como
   camada única de framing em vez de duplicar leitura de header/body.
