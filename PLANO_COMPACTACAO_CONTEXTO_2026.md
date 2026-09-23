# Plano de Implementação: Otimização de Compactação de Contexto no HAOS (2026)

**Documento de Arquitetura e Engenharia**  
**Data:** 21 de Setembro de 2026  
**Status:** PROPOSTO / PLANEJADO  
**Alvo:** Repositório `HERMES-TURBO` / Sistema Operacional do Agente (HAOS v1.2)  
**Origem de Referência:** DeepSeek Harness (`toolResultPruner`), OpenCode (`canonical blocks / goals`), Microsoft ACON (`guidelines & entity preservation`).

---

## 1. Visão Geral e Diagnóstico

### 1.1 O Estado Atual do HAOS
O HAOS possui uma arquitetura de compactação baseada em:
1. **Fase 1 (Determinística):** `prune_tool_results_only` em `agent/context_compressor.py` podando tool results por truncamento de caracteres e stubs textuais de uma linha.
2. **Fase 2 (Auxiliar LLM):** Sumarização de turnos intermediários com modelos auxiliares (`auxiliary.compression`), protegendo o *Head* (instruções do usuário) e a *Cauda* (últimos turnos).
3. **Invariante Sagrado:** O *prompt caching* (prefix cache) não pode ser quebrado a cada turno. Poda frequente no meio do histórico invalida o KV-cache e multiplica o custo de API.

### 1.2 Oportunidades Identificadas no Ecossistema
* **DeepSeek Harness (DSH):** Utiliza *surface folding* determinístico com thresholds precisos em Unicode e substituição limpa de eventos de observação (`surfaceOp: replace`), sem resíduos de buffers parciais.
* **Microsoft ACON:** Prova que resumos discursivos livres causam degradação (*context distraction*) porque perdem variáveis de alta fidelidade: paths de arquivos tocados, hashes de commit, branches ativas e status de testes.
* **OpenCode:** Possui integração canônica com objetivos e proteção rígida da cauda em marcos de execução.

---

## 2. Matriz de Mapeamento: HAOS Local vs Referências Remotas

| Capacidade | Implementação Atual no HAOS | Referência Remota | Proposta para o HAOS |
| :--- | :--- | :--- | :--- |
| **Observation Masking Determinístico** | Truncamento parcial com stubs (`_TOOL_RESULT_SUMMARIZERS` em `agent/context_compressor.py:1505`) | DSH `toolResultPruner` (thresholds: 8192 limiar, 4096 head, 1024 tail) | Introduzir colapso determinístico total de outputs antigos assimilados (`[Observation masked: read_file('X') — 120 lines omitted]`). |
| **Preservação de Entidades Críticas** | Prompt de sumarização livre com augmentações leves (`_augment_summary_lean` em `agent/context_compressor.py:3340`) | Microsoft ACON (Guidelines de retenção de commits, paths, branches e contratos) | Exigir bloco canônico estrito `## Key Entities & Artifacts` na saída do modelo auxiliar de compressão. |
| **Sincronização com Tarefas** | Desacoplado do Kanban durante a compressão de contexto | OpenCode `opencode-context-compress` (`/goal` recovery and cleanup) | Integrar compaction boundary com fechamento de cards do Kanban no HAOS (`kanban_complete`). |
| **Reparo de Sequência & Alternância** | `repair_message_sequence` em `agent/agent_runtime_helpers.py:563` | DSH event-log reconstruction | Manter e reforçar: o reparador já elimina tool_calls órfãos e assistentes contíguos com eficácia comprovada. |

---

## 3. Arquitetura Detalhada das Alterações

### 3.1 Camada A: Observation Masking Determinístico (Zero-LLM Pruning)
**Arquivo afetado:** `agent/context_compressor.py`  
**Métodos:** `_demote_tool_result_at` (linhas 2675–2704) e `_summarize_tool_result` (linhas 1385–1531).

* **Lógica Proposta:**
  Quando uma ferramenta antiga (fora da cauda protegida `_prune_boundary`) tiver sua saída inspecionada:
  1. Se for ferramenta de leitura/inspeção sem efeitos colaterais (`read_file`, `search_files`, `web_extract`, `web_search`), colapsar para marcador compacto determinístico:
     ```
     [Observation masked: {tool_name}({args_summary}) — execution succeeded ({total_chars} chars omitted)]
     ```
  2. Se for execução de código ou terminal (`terminal`, `execute_code`), reter apenas o comando executado, código de saída e última linha de status:
     ```
     [Observation masked: terminal('{cmd}') -> exit 0, output omitted]
     ```
  3. Evitar manter "meio-termos" (fragmentos de 1.000 a 4.000 caracteres de arquivos lidos no passado) que poluem a atenção do modelo.

### 3.2 Camada B: Diretrizes ACON no Prompt de Sumarização (Modelo Auxiliar)
**Arquivos afetados:**  
* `agent/context_compressor_summary.py` (seções de prompt de sumarização)  
* `agent/context_compressor.py` (método `_generate_summary`, linhas 3338–3342)

* **Prompt Estruturado Injetado:**
  Adicionar a diretriz de extração mandatória:
  ```markdown
  You MUST retain the following structured blocks verbatim at the top of the summary:
  ### Active Artifacts & Environment
  - Modified/Created Files: [exact absolute paths]
  - Git Branches/Commits: [exact branch names and commit SHAs if mentioned]
  - Completed & Pending Milestones: [active task IDs and state]
  - Test/Execution Status: [passing/failing suites and error signatures]

  Followed by the concise chronological summary of what was accomplished and decided.
  ```

### 3.3 Camada C: Sincronização de Marco do Kanban
**Arquivos afetados:** `agent/turn_context_compaction.py` e `agent/conversation_compression.py`.
* Ao transicionar um card Kanban para `DONE` via `kanban_complete`, sinalizar ao compressor que o bloco de mensagens correspondente àquela sub-tarefa está liberado para `Observation Masking` agressivo no próximo ciclo de `preflight`.

---

## 4. Plano de Fases de Execução e Verificação

### Fase 1: Implementação do Observation Masking Determinístico
- [ ] Adicionar função `mask_stale_observation(tool_name, tool_args, content)` em `agent/context_compressor.py`.
- [ ] Conectar nos pontos de demote determinístico (`_demote_tool_result_at`).
- [ ] Configurar flags de limiares no `config.yaml` (`compression.observation_masking.enabled: true`).
- [ ] **Testes:**
  - Rodar `scripts/run_tests.sh tests/agent/test_context_compressor.py`
  - Validar garantia de que `tool_call_id` permanece intacto e nenhum par `tool_call` ↔ `tool_result` é quebrado.

### Fase 2: Formatação Estruturada ACON no Sumarizador Auxiliar
- [ ] Atualizar templates em `agent/context_compressor_summary.py` com o esquema de artefatos.
- [ ] Validar parser em `agent/context_compressor.py::_generate_summary` para garantir que o bloco `### Active Artifacts & Environment` não seja descartado nas iterações de compressão em cascata.
- [ ] **Testes:**
  - Teste de compressão multi-turnos simulando histórico longo com arquivos e git commits.
  - Verificar se paths de arquivos persistem após 3 ciclos de compactação.

### Fase 3: Validação de Prompt Caching e Estabilidade
- [ ] Medir estabilidade de prefix cache via mock runner.
- [ ] Assegurar que a histerese (`proactive_prune_rearm_tokens`) impede que o mascaramento rode a cada turno único.
- [ ] Rodar suíte completa do core do agente: `scripts/run_tests.sh tests/agent/`.

---

## 5. Riscos Residuais e Mitigações

1. **Risco de Amputação de Contexto (O modelo esquecer o que leu):**
   * *Mitigação:* As ferramentas continuam disponíveis (`read_file`, `web_extract`). Caso o agente precise rever um arquivo lido há 20 turnos, ele pode relê-lo sob demanda em vez de pagar tokens em todos os turnos subsequentes.
2. **Invalidação de Cache de Provedores Rígidos:**
   * *Mitigação:* Manter a regra de ouro do HAOS: podas determinísticas só são comitadas se o ganho de tokens compensar a quebra do cache (`proactive_prune_min_reclaim_tokens`).
3. **Erros de Formato de Provider (Anthropic / OpenAI API 400):**
   * *Mitigação:* Nunca deletar a mensagem de retorno da tool; sempre preservar o envelope `{ "role": "tool", "tool_call_id": id, "content": ... }`, alterando apenas o payload do `content`.

---
*Plano gerado e consolidado via HAOS Engine com base em evidências do código local e literatura de 2026.*
