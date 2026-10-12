# Integração de Capacidades de Engenharia Reversa no HAOS

**Data:** 2026-10-12  
**Branch:** `research/re-skills-adoption`  
**Referência de Decisão:** `ADR-033-incorporacao-skills-engenharia-reversa-triagem-radare2-ghidra-broker.md`  

---

## 1. Visão Geral da Arquitetura

O HAOS absorveu seletivamente as capacidades mais maduras e comprovadas de engenharia reversa do ecossistema open-source em uma arquitetura de 3 camadas de execução orquestradas por um roteador fino e protegidas por um broker de permissões fail-closed:

```
                                  [ Tarefa de RE ]
                                          │
                                          ▼
                         [ hermes.platform.re.router ]
                        (RERouter / skill re-router)
                                          │
                  ┌───────────────────────┼───────────────────────┐
                  ▼                       ▼                       ▼
            [ Camada 1 ]            [ Camada 2 ]            [ Camada 3 ]
           Triagem Rápida        Backbone Scriptável      Análise Profunda
         (binary_analyzer)         (radare2 / r2mcp)        (GhidraMCP)
                  │                       │                       │
                  └───────────────────────┼───────────────────────┘
                                          │
                                          ▼
                           [ REPermissionBroker ]
                     - Grants exatos (sem curinga)
                     - Hash SHA-256 do binário alvo
                     - Verificação estrita de allowlist
                     - Timeouts assimétricos (abort em escrita)
                     - Decision Log forense
                                          │
                                          ▼
                            [ apply_mcp_result_budget ]
                     - Proteção de contexto do modelo
                     - Spillover seguro em ~/.haos/mcp-spills/
```

---

## 2. As 3 Camadas de Engenharia Reversa

### Camada 1: Triagem Estática Preliminar (`skills/reverse-engineering/binary-triage`)
- **Origem:** `masriyan/claude-code-cybersecurity-skill` (Skill 04).
- **Componente:** `skills/reverse-engineering/binary-triage/scripts/binary_analyzer.py`.
- **Finalidade:** Inspeção estrutural ultrarrápida (<50ms, sem overhead de processo de JVM/Ghidra).
- **Capacidades:**
  - Extração de cabeçalhos ELF/PE/Mach-O (arquitetura, endianness, entrypoint).
  - Verificação de mitigações de segurança (`checksec`): PIE, NX/DEP, Stack Canary, RELRO.
  - Cálculo de distribuição de entropia de Shannon (detecção de compactação/criptografia).
  - Filtragem de strings de alto interesse (URLs, IPs, caminhos de arquivo, credenciais).

### Camada 2: Backbone Scriptável radare2 (`hermes/platform/re/r2_backbone.py`)
- **Origem:** radare2 oficial + `radareorg/radare2-mcp` (v1.8.8 compilado nativamente contra `libr`).
- **Componente:** `Radare2MCPClient` e `/usr/local/bin/r2mcp`.
- **Finalidade:** Desmontagem em assembly, análise de fluxo de controle (CFG), listagem de funções (`afl`), imports/exports e scriptabilidade ágil.
- **Integração:** Protocolo MCP sobre JSON-RPC stdio. Protegido pelo mecanismo canônico de orçamento e spill-to-file (`apply_mcp_result_budget`).

### Camada 3: Análise Profunda e Decompilação C Ghidra (`hermes/platform/re/ghidra_mcp.py`)
- **Origem:** `LaurieWired/GhidraMCP` + `ghidra 12.1.2` + `PyGhidra` + JDK 26.
- **Componente:** `GhidraMCPClient` e extensão `/opt/ghidra/Extensions/Ghidra/GhidraMCP/lib/GhidraMCP.jar`.
- **Finalidade:** Reconstrução de algoritmos e pseudocódigo C de alto nível (`decompile_function`), mapas de tipos e análise profunda de binários complexos.
- **Integração:** Suporte a execução headless isolada com controle de lifecycle e timeouts assimétricos longos com abort em caso de estouro.

---

## 3. Roteamento Econômico (`re-router`)

Para garantir que o agente não desperdice tokens ou latência acionando o Ghidra para perguntas triviais:
- **`RERouter.select_tier()`**:
  - Questões de cabeçalho, entropia, PIE/NX, strings ou binário desconhecido $\rightarrow$ **`RETier.TRIAGE`**.
  - Questões de instruções, blocos básicos, opcodes, xrefs, funções em assembly $\rightarrow$ **`RETier.BACKBONE`**.
  - Questões de decompilação, reconstrução em C, tipos de dados e lógica algorítmica $\rightarrow$ **`RETier.DEEP`**.

---

## 4. Permission Broker e Governança Dual-Use (`hermes/platform/re/broker.py`)

Ferramentas de análise de binários são intrinsecamente de duplo uso. Nenhuma chamada de ferramenta é executada fora do controle:
1. **Grants Exatos Obrigatórios:** Proibidos curingas (`re.*`). Cada ferramenta declara e exige sua capability específica (`triage.inspect`, `r2.read_functions`, `r2.disassemble`, `r2.decompile`, `r2.write_patch`, `ghidra.decompile`, `ghidra.modify_program`).
2. **SHA-256 Target Hashing:** O hash criptográfico do alvo é computado e carimbado em toda entrada de decisão para auditoria forense não-repudiável.
3. **Allowlist Canônica:** `/bin`, `/usr/bin`, `/usr/local/bin` e caminhos sob os diretórios de teste do repositório são permitidos para leitura. Alvos externos exigem aprovação explícita do operador humano.
4. **Timeouts Assimétricos:** Operações de leitura expiram limpamente; operações mutantes ou com potencial de alteração de binário disparam `abort` fail-closed caso sofram timeout.
5. **Decision Log:** Persistência de auditoria em `~/.haos/decision-logs/re_audit.jsonl`.

---

## 5. Suíte de Avaliação e Testes

- **`tests/re/` (17 testes automatizados):**
  - `test_triage_skill.py`: Carregamento da skill de triagem e execução do analyzer.
  - `test_r2_backbone.py`: Conexão com r2mcp, enumeração de funções e spillover sob cota reduzida.
  - `test_ghidra_mcp.py`: Ciclo de vida do daemon, decompilação, spillover e abort em timeout.
  - `test_reverse_skill_cherrypick.py`: Metadados do router, ausência de duplicação e classificação determinística de tiers.
  - `test_re_permissions.py`: Rejeição de curingas, barreiras de allowlist, hash SHA-256 e política de timeouts.
  - `test_eval_harness.py`: Execução dos 4 cenários declarativos em `evals/re/scenarios/`.
- **Evals RE (`evals/re/`):** 4/4 cenários validados de forma determinística (Triagem, Desassembly, Decompilação, Negação).
- **Auditoria de Skills:** 226 skills auditadas com 0 violações via `scripts/audit_skills.py`.
- **Suíte de Integridade de Memória:** 486 testes verdes em `tests/platform/memory/` e `tests/platform/context/`.
