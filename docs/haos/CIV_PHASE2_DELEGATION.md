# Delegação Ativa → Civilização (Fase 2)

## Visão Geral
Conecta as sessões do Hermes (WebUI e Gateway) ao motor de roteamento da Civilização (Tier 1 a 4), permitindo especialização automática de bots (ex: `reverse-bot`, `research-bot`, `generic-bot`) mantendo total compatibilidade e rollback instantâneo via flags.

---

## 1. Fases Implementadas

### Fase 2A: Modo Observação (Ponto de Entrada)
- **Comportamento:** Toda mensagem recebida no WebUI (`tui_gateway/prompt_turn.py`) passa por `route_task_observe()`.
- **Efeitos Colaterais:** **Zero**. Não cria bots, não muta o EventStore e não altera o comportamento do turno se a delegação estiver desativada.
- **Log:** Emite `[CIV_OBSERVE]` indicando qual bot seria escolhido (`reverse-bot`, `research-bot`, `generic-bot`, ou `None` para saudações/chit-chat não delegáveis).

### Fase 2B: Delegação Opt-in & Fallbacks
- **Ativação:** Variável de ambiente `HAOS_CIV_DELEGATE=1` ou parâmetro de sessão `session["civ_delegate"] = True`.
- **Comportamento:** Quando ativa e a task for delegável, `apply_civ_bot_to_session()` aplica o bot especialista e sua identidade (`IdentityBundle`) ao agente da sessão.
- **Resiliência:** Qualquer falha na resolução do especialista realiza fallback silencioso e instantâneo para o fluxo normal do agente.

### Fase 2C: Auto-criação Controlada & Quotas
- **Flag:** `HAOS_CIV_AUTO_CREATE=1` (Default: `0` / Desativado por segurança).
- **Limite por Sessão:** Máximo padrão de 3 bots auto-criados por sessão (`HAOS_CIV_MAX_AUTO_CREATE_PER_SESSION=3`).
- **Auditoria:** Todo bot criado emite evento auditável `civ_bot_auto_created` no `EventStore` e log estruturado `[CIV_AUTO_CREATE_AUDIT]`.
- **Fallback:** Se a cota for atingida ou a flag estiver desativada, faz fallback para `generic-bot`.

### Fase 2D: Integração no Gateway & Rollback Instantâneo
- **Ativação:** Requer `HAOS_CIV_GATEWAY_ROUTING=1` (default 1) e `HAOS_CIV_DELEGATE=1`.
- **Comportamento:** O gateway vincula o especialista sugerido (`event._civ_suggested_bot`) ao turno do agente via `TurnContext.civ_bot_id` e `route_turn_to_specialist()`.
- **Rollback Instantâneo:** Se `HAOS_CIV_GATEWAY_ROUTING=0`, o gateway desativa o roteamento imediatamente e volta ao modo sombra (advisory only).

---

## 2. Matriz de Flags e Variáveis de Ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `HAOS_CIV_ENABLED` | `1` | Master switch da Civilização. Se `0`, desativa toda a camada. |
| `HAOS_CIV_DELEGATE` | `0` | Ativa a delegação ativa em sessões WebUI e Gateway. |
| `HAOS_CIV_AUTO_CREATE` | `0` | Permite auto-criação de bots especialistas para tarefas sem cobertura. |
| `HAOS_CIV_MAX_AUTO_CREATE_PER_SESSION` | `3` | Teto máximo de bots auto-criados por sessão. |
| `HAOS_CIV_GATEWAY_ROUTING` | `1` | Ponte do Gateway. Se `0`, força rollback para modo sombra. |

---

## 3. Procedimento de Rollback

Em caso de qualquer anomalia operacional:
```bash
# Rollback imediato no Gateway (retorna ao modo sombra advisory)
export HAOS_CIV_GATEWAY_ROUTING=0

# Desativar delegação ativa em novas sessões
export HAOS_CIV_DELEGATE=0

# Desativar auto-criação de bots
export HAOS_CIV_AUTO_CREATE=0
```
Nenhum reinício forçado é obrigatório; as variáveis são consultadas dinamicamente a cada turno.
