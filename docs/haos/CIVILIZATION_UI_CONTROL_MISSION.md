# Civilization UI — Control Plane e Mission Center

## Escopo e limites verificados

A superfície é o dashboard Hermes (`web/`, normalmente porta 9119), não o DeepSeek Harness na porta 3080.

O backend persiste configuração em `civ_agents.json` e o registro de missões em `civ_missions.json` no perfil ativo, e sincroniza controle canônico em `MissionStore` (`kanban.db`, escopo por perfil). Os controles start/pause/resume **acionam o runtime**: `MissionSupervisor` (`hermes/platform/execution/mission_runtime.py`) compila os nós do workflow em cartões Kanban, executa via `LaneWorker` com cancelamento cooperativo por `cancel_event`, e pausa/retoma por admission gate no desired state. Desativação de agente bloqueia create/start/resume de missões que o referenciam, sem matar processos externos em andamento (o cancelamento é cooperativo).

O catálogo disponível é o catálogo publicado pelo endpoint de Civilization. Valores de preço/contexto são metadados desse catálogo, não cotação em tempo real nem comprovação de acesso ao provedor.

## Fluxos de operação

### Agentes
- Exportar usa POST no endpoint canônico; o navegador baixa JSON e libera a URL temporária.
- Importar lê um arquivo JSON, mostra preview e exige confirmação antes de persistir. O endpoint substitui um agente com ID existente; a UI deve sinalizar a substituição.
- Disable/Enable preserva configuração/histórico e usa lifecycle explícito. Não constitui desligamento de um processo externo.

### Model Registry
- Usa o mesmo catálogo do editor: `name`, `provider`, `context_length`, custos input/output por milhão de tokens.
- Metadados ausentes devem aparecer como indisponíveis, nunca como preço zero inventado.

### Replay
- Reprodução é uma projeção local e somente leitura do histórico disponível da missão.
- Cursor, passos e velocidades não geram ações no runtime.
- Só estados de nós explicitamente registrados são reconstruídos; estados atuais do DAG não devem ser apresentados como históricos.
- Eventos legados podem não conter todos os campos necessários. A interface deve indicar a cobertura limitada.

### Aprovações
- A lista combina gates canônicos do runtime (`approval_gates` em `kanban.db`, fonte `canonical_runtime`) e pedidos explicitamente registrados no EventStore ainda não decididos.
- Nós com `requires_approval: true` produzem gates reais durante a execução; o supervisor segura a tarefa enquanto o gate está pendente.
- Uma decisão aprova/rejeita o gate transacionalmente, registra evento de auditoria no EventStore e acorda o `MissionSupervisor` (`wake()`); gate aprovado retoma a execução da tarefa, rejeitado marca a tarefa como falha.
- Pedidos inexistentes retornam 404; decisões repetidas/conflitantes retornam 409.

### Analytics
- Quando a missão foi executada pelo runtime, as métricas são medidas a partir de `MissionStore`: `tasks_total`, `tasks_completed`, falhas, retries (`consecutive_failures`), tokens e duração — tokens vêm de `execution_usage` registrado por tarefa.
- Tempo deve derivar de timestamps de início/fim efetivamente registrados, não de criação da especificação.
- Tokens reais dependem de usage correlacionado à missão; não usar tamanho de texto nem estimativa da simulação.
- Falhas/retries dependem de eventos instrumentados; ausência de instrumentação é `null`, não zero.
- Para missões apenas declarativas (sem execução canônica), `tokens`, `failures` e `retries` permanecem `null` com limitações documentadas; na instrumentação legada, `team.formed` → `mission.completed` permite calcular duração quando ambos estão registrados.
- Qualidade e limitações acompanham os números na API/UI.

## Contratos HTTP

Todas as rotas usam a autenticação de sessão existente e `?profile=`; arquivos, eventos e decisões pertencem ao perfil selecionado.

| Operação | Rota | Contrato |
|---|---|---|
| Transferência | `POST agents/{id}/export`, `POST agents/import` | JSON portátil; import valida escopo GLOBAL/PROMOTER |
| Disponibilidade | `POST agents/{id}/lifecycle` | `{enabled: boolean}`; retorna configuração enriquecida |
| Catálogo | `GET models/available` | Catálogo compartilhado com editor |
| Histórico | `GET missions/{id}/events` | `{events: [...]}`; mantém campos legados e identificadores registrados |
| Aprovações | `GET approvals` | Pendentes; filtro opcional `mission_id`; qualidade/limitações |
| Decisão | `POST approvals/{id}/decision` | `{approved: boolean, reason?: string}`; gates canônicos aprovados acordam o supervisor e reportam `runtime_resumed: true`; projeção por EventStore reporta `false` |
| Métricas | `GET missions/{id}/analytics` | Duração, tokens, falhas, retries, event_count, qualidade/limitações |

Prefixo das rotas: `/api/civilization/`. Transições de estado aceitas: simulate/start em DRAFT ou READY, pause em RUNNING, resume em PAUSED. Transições inválidas retornam 409; agente desconhecido retorna 400 e desativado retorna 409 ao criar/iniciar/retomar missão.

## Validação

Testes Python pelo runner `scripts/run_tests.sh`; componentes/helpers pelo Vitest em `web/`; typecheck e build pelos scripts do pacote.

Resultados observados nesta entrega: backend com 65 testes aprovados e 0 falhas em 5 arquivos — 9 Civilization router (aprovação concorrente, autenticação, isolamento A→B→A e redaction do replay), 35 adjacentes de perfil e 21 Civilization/kernel. Frontend: suíte completa com 55 arquivos e 376 testes aprovados; typecheck e build de produção aprovados. ESLint no escopo alterado: 0 erros e 11 warnings (hooks/compiler), não warning-free. Sintaxe Python e `git diff --check` sem erros. Validação visual autenticada do servidor existente permanece pendente.

O build de `web/` gera `hermes_cli/web_dist/`. Validar a superfície Hermes existente após rebuild/refresh e, se necessário, reinício aprovado do backend para carregar novas rotas. Não iniciar um servidor substituto para alegar atualização do servidor existente.

## Checklist de validação manual autenticada

1. No perfil A, exportar um agente e importar o arquivo: conferir preview, confirmação de substituição e mensagem de erro para JSON inválido.
2. Desativar um agente; verificar configuração preservada e bloqueio de novas missões/start/resume que o referenciam. Reativar sem criar outra identidade.
3. Abrir Models: comparar contexto e USD por milhão de tokens; filtrar provedor/nome; valores desconhecidos não viram zero.
4. No Mission Center, reproduzir Next/Previous, Start/End, slider, Play/Pause e velocidades 1×/2×/10×. Trocar missão/perfil durante playback e confirmar limpeza do timer.
5. Conferir Approval Center global, inclusive pedidos sem mission_id; aprovar/rejeitar um pedido real registrado e verificar remoção da pendência após sucesso. Repetição deve falhar com 409.
6. Conferir métricas ausentes como Unknown e duração apenas com eventos reais. Não confundir estimativa da simulação com gasto real.
7. Alternar A→B→A durante requests: nenhum resultado ou erro do perfil antigo deve atualizar a nova tela; mutações devem manter o perfil capturado.

Não popular eventos falsos no perfil de produção para satisfazer este checklist. Pedidos/métricas reais dependem da instrumentação explicitada acima.

## Fora de escopo

A execução end-to-end (compilação do workflow em cartões Kanban, gates humanos duráveis, usage medido por tarefa, pause/resume cooperativo e recuperação de missões ativas) foi entregue em `hermes/platform/execution/mission_runtime.py` + `mission_store.py` e é coberta por `tests/platform/execution/test_mission_integration_e2e.py`. Permanecem fora: executor LLM real por padrão no control plane (o worker de produção exige `HermesCliLaneWorker.available()`; sem ele o fallback determinístico é marcado como `provider="deterministic"` nos dados medidos), interrupção abrupta de processos externos (apenas cancelamento cooperativo) e qualquer capacidade que dependa de botões sem endpoint correspondente.
