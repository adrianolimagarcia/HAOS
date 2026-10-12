# Mapeamento e Reconhecimento: Skills de Engenharia Reversa -> HAOS (Fase 0)

**Data:** 2026-10-12  
**Branch:** `research/re-skills-adoption`  
**Base commit:** `ef3ad54ce2`  
**Status da suíte de memória:** PASSING (486 passed em `tests/platform/memory/` e `tests/platform/context/`)

---

## 1. Mapeamento de Componentes e Gaps

| Peça a incorporar | Onde mora no HAOS (arquivo:linha) | Formato esperado | Gap confirmado |
|---|---|---|---|
| **1. Triagem Rápida**<br>(masriyan/claude-code-cybersecurity-skill - skill 04) | `skills/reverse-engineering/binary-triage/SKILL.md`<br>Descoberta: `agent/skill_utils.py:413`<br>Parsing frontmatter: `agent/skill_utils.py:118`<br>Carga de payload: `agent/skill_commands.py:155` | Diretório de skill padrão com `SKILL.md` (frontmatter YAML com descrição contendo trigger `<= 57` chars) + playbook de triagem e script auxiliar em `scripts/binary_analyzer.py` (usando dependências python puras/pip como `pefile`, `capstone`, `pyelftools`). | O HAOS não possui playbook nativo nem ferramentas especializadas para inspeção preliminar de binários (extração de cabeçalhos ELF/PE/Mach-O, verificação de mitigações de segurança NX/PIE/Canary, seções e strings críticas) antes de recorrer a desassembladores pesados. |
| **2. Backbone radare2**<br>(radareorg/radare2-mcp / radare2) | Integração MCP: `hermes/platform/re/r2_backbone.py`<br>Orçamento & Spill: `hermes/platform/mcp/spill.py:47`<br>Binário: `/usr/local/bin/r2mcp` (v1.8.8) | Servidor/Bridge MCP via stdio expondo comandos essenciais do radare2 (`open_file`, `analyze`, `list_functions`, `disassemble_function`, `decompile_function`), acoplado a `Radare2MCPClient` e `apply_mcp_result_budget` para spill-to-file (`~/.haos/mcp-spills/`) em desmontagens que excedam o limite. | Resolvido na Fase 2: `radare2 6.2.2-1` instalado via `pacman -S radare2`. `r2mcp 1.8.8` compilado em C nativo contra `libr` a partir de `radareorg/radare2-mcp` e instalado em `/usr/local/bin/r2mcp`. Operação validada via JSON-RPC stdio. |
| **3. Ghidra Headless**<br>(LaurieWired/GhidraMCP) | Daemon & MCP: `hermes/platform/re/ghidra_mcp.py`<br>Extensão Ghidra: `/opt/ghidra/Extensions/Ghidra/GhidraMCP/`<br>Orçamento & Spill: `hermes/platform/mcp/spill.py:47` | Servidor MCP Python que orquestra o daemon do Ghidra (`analyzeHeadless`/PyGhidra), expondo funções de alto nível: decompilação em pseudocódigo C (`decompile_function`), callgraphs e enumeração de símbolos. Sujeito a timeout assimétrico longo e spill-to-file. | Resolvido na Fase 3: `ghidra 12.1.2-2.1` instalado no host. `GhidraMCP.jar` compilado com Java 26 e instalado no diretório de extensões do Ghidra. Cliente `GhidraMCPClient` em `hermes/platform/re/ghidra_mcp.py` com timeout assimétrico e spill-to-file (`~/.haos/mcp-spills/`). |
| **4. Roteador de RE (Cherry-pick)**<br>(zhaoxuya520/reverse-skill) | Roteador: `skills/reverse-engineering/router/SKILL.md`<br>Descoberta: `agent/skill_utils.py:413`<br>Parsing frontmatter: `agent/skill_utils.py:118` | Skill fina de roteamento declarativo (Tool Index) instruindo o modelo a selecionar a ferramenta adequada para cada tarefa (Triagem rápida -> radare2 -> Ghidra), descartando redundâncias e plataformas fora de escopo (firmwares Apple, iOS, Mach-O proprietários). | Ausência de taxonomia e roteador econômico de ferramentas no HAOS, o que causaria sobrecarga de tokens e latência se o agente tentasse acionar Ghidra ou r2 para tarefas triviais resolvidas por inspeção de cabeçalhos. |

---

## 2. Permission Broker, Auditoria e Dual-Use (Fase 5)

As ferramentas de engenharia reversa são utilitários de duplo uso (dual-use), capazes de analisar e interagir com qualquer binário. Para garantir a segurança:
- **Localização no HAOS:** `tools/approval.py:1149` (`check_all_command_guards`), `tools/approval_context.py:27`, e implementação dedicada em `tools/re_broker.py` ou `hermes/platform/security/re_broker.py`.
- **Grants Exatos:** Nenhuma permissão curinga (`re.*`). Cada operação exige grant explícito (ex: `r2.read_functions`, `r2.disassemble`, `ghidra.decompile`).
- **Target Hashing:** Registro obrigatório do hash SHA-256 do binário alvo em cada entrada do Decision Log.
- **Allowlist Inicial:** Binários do sistema (`/bin`, `/usr/bin`) e caminhos de teste sob o repositório (`$DEV/tests/`) têm execução permitida para leitura. Qualquer arquivo fora dessa allowlist exige card explícito de aprovação do operador humano.
- **Timeouts Assimétricos:** Operações de análise estática e leitura possuem timeout generoso com descarte limpo; operações mutantes ou potencialmente destrutivas disparam `abort` fail-closed no timeout.

---

## 3. Confirmação do Ambiente do Host (cachyosdell)

Consultado via `pacman -Si` em 2026-10-12:
- **radare2:** Versão `6.2.2-1` no repositório `[extra]` (Tamanho de download: ~3.7 MiB; Instalado: ~36.8 MiB).
- **ghidra:** Versão `12.1.2-1` no repositório `[extra]` (Tamanho de download: ~302 MiB; Instalado: ~386 MiB).
- **jdk-openjdk:** Disponível no repositório `[extra]`.
- **Suíte de Memória Existente:** 486/486 testes verdes em `tests/platform/memory/` e `tests/platform/context/`.
