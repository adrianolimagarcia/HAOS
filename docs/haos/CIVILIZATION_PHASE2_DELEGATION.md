# Civilização: Fase 2 — Delegação Ativa (WebUI & Gateway)

Este documento descreve a arquitetura, flags de controle, ciclo de vida e procedimentos de rollback para a **Fase 2 da Delegação na Civilização** do HAOS (Hermes Agent Operating System).

---

## 1. Visão Geral

A Fase 2 conecta as sessões interativas (WebUI/TUI Gateway) e o Gateway principal ao ecossistema de bots especializados da Civilização, garantindo que tarefas elegíveis sejam roteadas deterministicamente para o melhor especialista existente (`reverse-bot`, `research-bot`, etc.) ou generalista (`generic-bot`), preservando isolamento e segurança operacional.

---

## 2. As Quatro Fases de Implementação

### Fase 2A: Ponto de Entrada & Modo Observação
- **Objetivo:** Auditar e registrar em log qual bot seria selecionado para cada prompt sem alterar o fluxo de execução.
- **Função:** `route_task_observe(task_or_text, session_context=None, event_store=None)`
- **Comportamento:**
  - Tasks de engenharia reversa (`elf`, `ghidra`, `decompile`, etc.) indicam `reverse-bot`.
  - Tasks de pesquisa temporal (`recent-news`, `temporal`, `pesquisa`, etc.) indicam `research-bot`.
  - Tasks genéricas indicam `generic-bot`.
  - Saudações e bate-papo conversacional (`"olá"`, `"bom dia"`, `"tudo bem"`, etc.) retornam `None` e não são delegadas.
  - Zero mutação no estado e zero escritas no `EventStore` em modo observação.

### Fase 2B: Delegação Opt-in & Fallbacks Resilientes
- **Objetivo:** Ativação granular e segura por ambiente ou sessão.
- **Flag global:** `HAOS_CIV_DELEGATE=1` (Default: `0` / OFF).
- **Parâmetro por sessão:** `session["civ_delegate"] = True`.
- **Funções:** `civ_delegation_enabled(session)`, `apply_civ_bot_to_session(session, bot_id)`.
- **Invariante de Resiliência:** Qualquer falha na resolução do bot especialista ou bundle de identidade executa fallback imediato para o fluxo normal do agente genérico (`generic-bot` ou identidade padrão), sem jamais interromper a sessão do usuário.

### Fase 2C: Auto-Criação Controlada & Quota de Sessão
- **Objetivo:** Criação controlada de bots especialistas caso nenhuma cobertura exista e a heurística aprove.
- **Flag global:** `HAOS_CIV_AUTO_CREATE=1` (Default: `0` / OFF por segurança).
- **Limite por sessão:** Máximo de 3 bots auto-criados por sessão (`HAOS_CIV_MAX_AUTO_CREATE_PER_SESSION=3`).
- **Auditoria:** Toda auto-criação emite o evento `civ_bot_auto_created` estruturado no `EventStore` com `correlation_id` e timestamp. Se o teto da sessão for atingido, fallback automático para `generic-bot` com log `[CIV_AUTO_CREATE_AUDIT]`.

### Fase 2D: Integração no Gateway & Rollback Instantâneo
- **Objetivo:** Roteamento ativo de mensagens no gateway para especialistas selecionados.
- **Flag global:** `HAOS_CIV_GATEWAY_ROUTING=1` (Default: `1`).
- **Rollback instantâneo:** Definir `HAOS_CIV_GATEWAY_ROUTING=0` reverte **instantaneamente** o gateway ao modo sombra (Fase 1 / advisory only), ignorando o roteamento ativo.
- **Funções:** `gateway_active_routing_enabled(session)`, `route_turn_to_specialist(agent, bot_id)`.

---

## 3. Matriz de Variáveis de Ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `HAOS_CIV_ENABLED` | `1` | Kill-switch mestre da Civilização. Se `0`, desativa toda a camada. |
| `HAOS_CIV_DELEGATE` | `0` | Ativa a delegação ativa em sessões WebUI e Gateway. |
| `HAOS_CIV_AUTO_CREATE` | `0` | Permite auto-criação de novos bots via router. |
| `HAOS_CIV_MAX_AUTO_CREATE_PER_SESSION` | `3` | Teto de segurança de auto-criações permitidas por sessão. |
| `HAOS_CIV_GATEWAY_ROUTING` | `1` | Habilita a ponte do gateway (se `0`, força rollback para modo sombra). |

---

## 4. Guia de Ativação e Rollback

### Para Ativar em Produção (Opt-in Gradual):
```bash
# Ativar delegação ativa com auto-criação protegida:
export HAOS_CIV_DELEGATE=1
export HAOS_CIV_AUTO_CREATE=0   # Manter 0 inicialmente para usar apenas bots existentes
```

### Para Rollback Imediato (Emergência):
```bash
# 1. Rollback para modo sombra no Gateway (sem roteamento ativo):
export HAOS_CIV_GATEWAY_ROUTING=0

# 2. Desativação completa da delegação em sessões:
export HAOS_CIV_DELEGATE=0

# 3. Kill-switch mestre total (desliga qualquer intervenção da civilização):
export HAOS_CIV_ENABLED=0
```

Nenhum reinício destrutivo de banco ou serviço é necessário. A mudança de flags tem efeito imediato no próximo turno de execução.
