# TODO HAOS — Backlog Arquitetural de Absorção (OpenAI Stack 2026)

Este documento reúne as melhorias e padrões de arquitetura avaliados a partir dos ecossistemas `openai-agents-python` e `openai-cua-sample-app` para incorporação planejada no HAOS.

> **Status:** PLANEJADO / NÃO IMPLEMENTAR AINDA (Backlog formal)  
> **Data:** 21 de Setembro de 2026

---

## 1. Handoffs com Validação Estrita de Schemas (`input_type` / Pydantic)

* **Origem & Referência:** `openai/openai-agents-python` (`openai_agents.handoffs.Handoff` com validação de `input_type` e Pydantic).
* **O que é:** Ao transferir a execução de um agente gestor (ex: `orquestrador`) para um especialista (ex: `forge_coder`, `pixel_front`), o modelo não passa uma instrução solta em texto livre; ele deve preencher um payload estruturado, tipado e validado deterministicamente por schema.
* **Problema Resolvido no HAOS:** Elimina alucinação de argumentos ou esquecimento de metadados obrigatórios (ex: IDs de cards, branches git, arquivos afetados) na chamada de `delegate_task` e no dispatch do The Eye.
* **Aplicação Técnica no HAOS:**
  * Arquivo alvo: `tools/delegate_tool.py` e `agent/agent_delegation.py`.
  * Adicionar parâmetro e validador `input_schema` / `task_payload` opcional nas tarefas despachadas.
  * O despachante valida localmente o payload antes de invocar o subagente filho. Caso inválido, devolve erro sintático imediato para autocorreção do pai antes de consumir turnos.

---

## 2. Controle de Concorrência de Ferramentas (`max_function_tool_concurrency`)

* **Origem & Referência:** `openai/openai-agents-python` (`RunnerConfig.max_function_tool_concurrency`).
* **O que é:** Limitação explícita de paralelismo na execução concorrente de tool calls emitidas no mesmo turno pelo LLM.
* **Problema Resolvido no HAOS:** Quando o modelo emite múltiplos `patch`, `write_file` ou comandos concorrentes de `terminal` de uma só vez, podem ocorrer disputas de I/O de disco, colisões de *file locks* ou sobrecarga de processos no sistema operacional.
* **Aplicação Técnica no HAOS:**
  * Arquivo alvo: `model_tools.py` (`handle_function_calls` / pool assíncrono).
  * Implementar um semáforo ou worker pool (ex: `asyncio.Semaphore(max_concurrency)`) com default em `3` para execuções paralelas de ferramentas locais.
  * Preservar a ordem exata de retorno exigida pelo protocolo de tool results.

---

## 3. Tripwire de Failsafe de Hardware (`release_inputs` pattern)

* **Origem & Referência:** `openai/openai-cua-sample-app` (`python-app/app/desktop/release_inputs.py`).
* **O que é:** Rotina determinística de segurança de hardware que força o envio de `KeyUp` para todas as teclas físicas e `MouseUp` para todos os botões de clique do mouse caso o agente morra, tome timeout ou seja cancelado pelo operador.
* **Problema Resolvido no HAOS:** Ao usar a ferramenta `computer_use` (via `cua-driver`), se o agente for interrompido enquanto estiver arrastando uma janela (mouse pressionado) ou digitando com `Ctrl`/`Shift`/`Alt` pressionado, o desktop Linux do operador não fica com teclas ou botões travados.
* **Aplicação Técnica no HAOS:**
  * Arquivo alvo: `tools/computer_use/cua_backend_input.py` e hooks de encerramento do `CuaDriverSession`.
  * Registrar manipuladores de emergência (`atexit` e ganchos de `SIGINT`/`SIGTERM` no driver) invocando `release_all_inputs()` através do MCP do `cua-driver`.
  * Garantir cobertura multiplataforma (Linux Wayland/X11 via `xdotool`/uinput/portal, Windows `user32` e macOS `Quartz`).

---

## 4. Matriz de Priorização e Próximos Passos

| Item | Complexidade | Risco de Regressão | Prioridade Sugerida |
|---|---|---|---|
| **1. Validação Estrita de Schemas (`delegate_task`)** | Média | Muito Baixo | Alta |
| **2. Concorrência de Tools (`max_concurrency`)** | Baixa | Muito Baixo | Alta |
| **3. Tripwire Failsafe de Teclado/Mouse (`release_inputs`)** | Baixa | Baixo | Média |
