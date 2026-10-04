# Auditoria — instalação HAOS versus main local

## Estado observado

- Workspace: `157cb09ca4aacf57f662dbee15e7a679c7082c5d` (main e origin/main localmente iguais; não foi feito fetch nesta auditoria).
- Instalação `/usr/local/lib/haos-agent`: HEAD `91887dfe951b21c42ef3c7b495ed518f4f717252`, mais alterações não commitadas.
- Comparação byte a byte dos 124 caminhos alterados/não rastreados na instalação: **83 iguais ao HEAD local**, **9 iguais somente ao working tree local**, **2 divergentes de working tree local também alterado**, **30 outros diferentes do HEAD local**.
- Divergência não significa novidade: versões da instalação são 0.21.45 versus 0.21.65, Cargo.toml omite cinco crates presentes no main, e várias alterações são funcionalidades já ampliadas no main.
- Não foram copiadas fontes, criados commits, publicados commits nem reiniciados serviços.

## Presentes localmente mas ainda não commitados (conteúdo igual à instalação)

- `gateway/run_adapters.py`: intervalo do handoff watcher 2→5 segundos.
- `hermes/platform/webui/controlplane.py`: helper de resolução de role/model/provider.
- `hermes_cli/web_routers/civilization.py`: rotas de Civilization (arquivo rastreado modificado).
- `hermes_cli/web_server.py`: registro do router memory_graph.
- `tools/terminal_scope.py`: cache de escopo de terminal por perfil/mtime.
- `tests/agent/test_api_content_cascade_prune.py`
- `tests/agent/test_builtin_prefetch_spill_bound.py`
- `tests/hermes_state/test_prune_stale_memory_context.py`
- `tests/hermes_state/test_reconcile_session_message_counts.py`

Não commitar esses nove arquivos isoladamente: Civilization e memory_graph têm dependências e testes em outras alterações locais. Testes presentes não significam testes executados.

## Dois caminhos com versões locais divergentes

- `hermes/platform/context/memory/vector_index.py`
- `hermes_cli/web_routers/memory_graph.py`

Necessário reconciliar hunks individualmente; não substituir a versão local pela instalada.

## Candidatos funcionais realmente não equivalentes

### Dream (`hermes/platform/memory/dream.py`)

A instalação acrescenta `_SYSTEM_NOISE_RE` / `_sanitize_session_preview`, limpeza de preview/fallback e palavras técnicas (kernel/network/hardware/server/infra) para reconciliação. O main atual não contém esse helper. Porém a instalação também remove proteções mais novas: `is_prompt_envelope(lesson)` e tratamento de retorno None de record_instinct. Recuperar somente hunks úteis preservando as proteções. O sanitizer ignora qualquer linha iniciada em '['/'<'; revisar falsos positivos. O teste instalado `tests/platform/memory/test_dream_sanitization.py` está ausente localmente; o segundo caso testa reconciler, não o roteamento Dream. Não executado.

### OKF (`hermes/platform/memory/okf.py`)

A instalação preserva metadata do item antes de preencher title/tags. Candidato útil não equivalente ao main. Também aplica heurística de fast-path baseada em 'pytest' e '/tmp/' no caminho: não copiar automaticamente; não constitui política de escopo segura.

### WebUI estática

Inspeção delegada encontrou chamadas locais para helpers de hierarquia (`fetchState`, `selectBotRole`, `filterHierModelDropdown`, `saveHierarchyNode`) cujas definições existem na instalação e foram removidas localmente, sem arquivos JS irmãos encontrados. **Possível regressão funcional, revisão estática; não validada em navegador.** Recuperação deve ser seletiva, sem restaurar o bloco antigo inteiro.

## Diferenças que não justificam copiar a instalação

- context_compressor e turn_context_compaction: main acrescenta ToolResultCompaction e caminho C-ABI preservando fallbacks.
- bots/manager, lane_executor e delegate_tool: main acrescenta identidade bot/Civilization; instalação omite isso. Ordenação CLI do lane_executor é divergente, não um bug provado.
- canonical_store: main acrescenta leitura/FTS nativa e mantém fallback SQL.
- haos_delegation_bridge: instalação usa 'haos-mayor', main usa 'haos-the-eye'.
- mcp_tool: main acrescenta preferência por haos-supervisor.
- haos-exec/main.rs: instalação é subconjunto; main contém mais comandos nativos e teste de segurança.
- harness_bindings.py: removido explicitamente em `9f9d47ef17`; não restaurar arquitetura antiga só porque o arquivo instalado existe.
- tools/file_tools.py e tools/the_eye_tool.py: diferenças apenas de whitespace pela comparação de tokens.
- packages/haos-edge/main.rs: instalação perde writer/profile/observer e módulos atuais; não substituir.
- compactor.rs: revisão delegada completa encontrou somente formatação, sem funcionalidade faltante.
- Cargo.lock: comparação TOML delegada encontrou zero pares (nome, versão) exclusivos da instalação; não recuperar lock antigo.
- vector_index.py: working tree acrescenta parent-child ausente tanto no HEAD quanto na instalação. Riscos estáticos antes de commit: falta de validação equivalente de dimensão/normalização; expansão do schema enquanto INSERT antigo usa VALUES sem lista de colunas; fallback ignora min_confidence/deduplicate_parent e fabrica scores/confidence=1.0. Não testados em runtime.
- memory_graph.py: não rastreado localmente; versão local é mais completa que a instalada. Revisar colisão de IDs de notas por basename e limites de leitura de arquivos no preview de código antes de commit.
- hermes/platform/decision/__init__.py: instalação adiciona reexports; spec/store/engine já existem localmente. Ausência não prova feature ausente.
- gateway/shutdown_forensics.py.bak-20260913-systemd-scope: backup, não candidato automático a commit.

## Conclusão

Grande parte do dirty da instalação já está no main. Há trabalho local não commitado e candidatos pontuais ainda não equivalentes. Não é seguro commit/pull indiscriminado. Preparar commits por domínio, com dependências e testes reais; cada commit em main exige bump patch pelo script release. Auditoria é REVIEWED (estática), não validação runtime nem autorização de prontidão de todo o conjunto.
