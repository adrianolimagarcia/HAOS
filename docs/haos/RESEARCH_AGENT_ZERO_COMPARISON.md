# HAOS vs. Agent Zero — Relatório Comparativo

> **Status:** relatório de pesquisa. Não é especificação nem ADR.
> **Data da medição:** 2026-09-16/17.
> **Bases medidas:** HAOS @ `62f8e86024` (repo local) · `agent0ai/agent-zero` @ clone shallow de `main` (`/tmp/a0`).

---

## 0. Método e classificação de evidência

| Classe | Significado neste relatório |
|---|---|
| **FATO** | Medido por comando executado; comando citado para reprodução |
| **EXECUTADO** | Código realmente rodado neste ambiente, com saída observada |
| **INFERÊNCIA** | Derivado logicamente dos fatos |
| **JULGAMENTO** | Avaliação subjetiva com critério declarado (as notas 0–100) |
| **UNVERIFIED** | Não verificado; declarado como tal |

As notas são **JULGAMENTO**, não medição. Os pesos estão explícitos na §3 para permitir discordância.

---

## 1. Inventário quantitativo (FATO)

### 1.1 Código

| Métrica | HAOS | Agent Zero | Razão |
|---|---:|---:|---:|
| Python — arquivos (fonte) | 2.253 | 686 | 3,3× |
| Python — LOC (fonte, s/ testes) | **836.594** | **116.715** | 7,2× |
| Python — LOC (total c/ testes) | 1.990.347 | 167.524 | 11,9× |
| Testes — arquivos | 4.614 | 176 | 26× |
| Testes — LOC | **1.153.753** | **50.809** | 22,7× |
| `def test_*` | **44.463** | **1.516** | 29× |
| Razão teste/fonte (LOC) | **1,38** | **0,44** | — |
| Arquivos > 2.000 LOC | **77** | n/a | — |
| Arquivos > 1.000 LOC | n/a | 30 | — |
| TS/JS LOC | ~768.000 | 34.426 | 22× |

Comandos:
```bash
# A0
find . -name '*.py' -not -path './tests/*' -print0 | xargs -0 cat | wc -l   # 116715
find tests -name '*.py' -print0 | xargs -0 cat | wc -l                       # 50809
grep -rho "def test_[a-zA-Z0-9_]*" tests/ | wc -l                            # 1516
find . -name '*.py' -not -path './tests/*' -print0 | xargs -0 wc -l | awk '$1>1000' | wc -l  # 30
```

### 1.2 Superfície de capacidade

| Eixo | HAOS | Agent Zero |
|---|---|---|
| Backends de terminal | **7** (`tools/environments/`: local, docker, ssh, modal, daytona, singularity, vercel_sandbox) | 1 (docker) |
| Plataformas de mensageria | **~20** (`gateway/platforms/`) | 2 (Telegram, WhatsApp) + email |
| Providers de memória | **9** (`plugins/memory/`) | 1 (FAISS, plugin opcional) |
| Plugins bundled | 28 dirs | **42** |
| Catálogo de plugins de terceiros | **101 entradas YAML** (100 com `repo:` + SHA 40-char) | 100+ no Plugin Hub |
| Skills (`SKILL.md`) | 67 + 151 optional | 7 |
| Pontos de extensão | hooks + ABCs + `gateway/builtin_hooks/` | **31** pontos de ciclo de vida + `_functions/<qualname>/<start\|end>` |
| Interfaces | CLI, TUI (Ink), Electron, web (25 routers), ACP, servidor OpenAI-compatible | Web UI (Alpine.js), Canvas, A0 CLI, ACP, Launcher |
| Workflows de CI | **37** | **2** |

### 1.3 Verificação de executabilidade (EXECUTADO)

**HAOS — suíte roda e passa:**
```
$ scripts/run_tests.sh tests/test_haos_default_home.py tests/test_display_hermes_home.py
=== Summary: 2 files, 6 tests passed, 0 failed (100% complete) in 4.5s (16 workers) ===
```

**Agent Zero — suíte NÃO é executável neste ambiente:**
```
$ python3 -c "import pytest"     → ModuleNotFoundError
$ python3 -c "import flask"      → ModuleNotFoundError
$ python3 -c "import litellm"    → ModuleNotFoundError
$ python3 -c "import faiss"      → ModuleNotFoundError
```
**UNVERIFIED:** não afirmei nem afasto a hipótese de a suíte do A0 passar. Ela simplesmente não pode ser executada aqui sem instalar ~67 dependências (incluindo `unstructured[all-docs]`, `openai-whisper`, `faiss-cpu`, LibreOffice).

---

## 2. Correções à análise preliminar

Duas conclusões iniciais foram **refutadas** pela verificação:

**CORREÇÃO 1 — O catálogo de plugins do HAOS não é decorativo.**
Eu havia contado "102 arquivos" e deixado como UNVERIFIED se eram reais. São **101 entradas YAML**, das quais **100 têm `repo:` com SHA de 40 caracteres**, mantenedor nomeado, `tier` e `category`. Zero stubs. Distribuição: 96 `community` + 4 `official`; categorias `tools` (25), `desktop` (18), `platform` (15), `web` (10), `general` (10), `memory` (9), `voice` (5), `automation` (5), `models` (1). Os repos apontam para mantenedores externos reais (Adolanium ×10, rriggs ×5, NousResearch ×4, wysie, TREE-Ind, kyssta-exe…).
O `plugin-catalog/README.md` define uma política de admissão com gate humano, SHA exato obrigatório e proibição de código auto-atualizável. **Isso é uma superfície de ecossistema real** — eu a havia pontuado em 1,5/10 com base apenas no fato de o repo ser privado.

**CORREÇÃO 2 — A nota de ecossistema do HAOS sobe de 1,5 para 4,0.**
Ressalva: o `plugin-catalog` é infraestrutura herdada do upstream Hermes (`README.md`: *"Entries are added only via a PR to the `hermes-agent` repository"*), não construída pelo HAOS. O crédito é parcial.

**Efeito no placar:** HAOS 82,5 → **83,5**.

---

## 3. Scorecard 0–100 (JULGAMENTO)

| # | Dimensão | Peso | HAOS | A0 | Fundamento |
|---|---|---:|---:|---:|---|
| 1 | Core loop, providers, prompt caching | 12 | **9,0** | 7,5 | HAOS trata cache-per-conversa como invariante arquitetural; A0 tem loop único em `agent.py` (1.613 LOC) |
| 2 | Ambiente de execução / isolamento | 8 | **9,0** | 8,5 | HAOS: 7 backends. A0: 1 backend, mas com desktop Linux real + runtime duplo (framework/execução) |
| 3 | Superfície de capacidade | 9 | **9,0** | 8,0 | HAOS mais uniforme e amplo; A0 mais focado |
| 4 | Extensibilidade | 9 | 8,5 | **9,5** | A0 ganha pelo pipeline `_plugin_installer` + `_plugin_scan` (scanner LLM) + `_plugin_validator` |
| 5 | Interfaces & UX | 9 | **9,5** | 7,5 | HAOS: 6 superfícies + 20 plataformas. A0: Canvas rico porém uma superfície |
| 6 | Orquestração multi-agente | 9 | **9,5** | 6,5 | HAOS: 8 spawn shapes, CPM/Kahn, PIP, backpressure, anti-anchoring, A2A/ACP/ANP |
| 7 | Memória & aprendizado | 7 | **8,5** | 6,0 | HAOS: 9 providers. A0: memória é plugin opcional |
| 8 | Segurança & safety | 8 | **8,5** | 7,0 | HAOS: 19 arquivos de approval/guard. A0: docker + ledger de segurança rigoroso |
| 9 | Testes & CI | 10 | **9,5** | 3,0 | **Maior gap.** A0: 2 workflows, nenhum roda teste; sem lint, sem typecheck |
| 10 | Docs & onboarding | 5 | 7,5 | **8,5** | A0 tem README/Launcher voltados ao usuário final |
| 11 | Ecossistema & comunidade | 4 | 4,0 | **9,5** | A0: 19,2k stars, 3,8k forks, 66 contribuidores |
| 12 | Manutenibilidade / saúde do código | 10 | 5,0 | **6,5** | HAOS: 836k LOC, 77 god files, 1,38 de razão teste/fonte |
| | **TOTAL PONDERADO** | **100** | **83,5** | **71,5** | |

**Sensibilidade dos pesos.** O resultado é dirigido pela escolha de pesos, não por margem robusta. Com ecossistema pesando 15 em vez de 4 e testes pesando 4 em vez de 10, o placar inverte. Os pesos refletem prioridade de *appliance* auto-hospedado, não de produto de massa.

---

## 4. Pontos fortes do HAOS

1. **Rigor de testes sem paralelo.** 44.463 testes; harness verificado em execução (§1.3); isolamento por subprocesso; política de flaky explícita; proibição documentada de change-detectors.
2. **CI de verdade.** 37 workflows: `osv-scanner.yml`, `supply-chain-audit.yml`, `tests-os.yml`, `windows-venv-e2e.yml` (E2E em runner Windows real), `install-e2e` em 3 SOs, `docker-lint.yml`, `lockfile-diff.yml`.
3. **Disciplina de dependências.** Upper bounds obrigatórios (`>=floor,<next_major`), SHA de 40 chars para git URLs, SHA+comentário para Actions.
4. **Orquestração multi-agente formal.** `hermes/platform/` (169 arquivos, 42.488 LOC): `SpawnResolver` com 8 formas canônicas; CPM via Kahn com forward/backward pass e slack; Priority Inheritance Protocol; `ConcurrencyGuard` com backpressure que não queima retries; `CompositeAcceptanceEngine` com `all_of`/`any_of` sobre validadores `structural`/`test`/`lsp`/`schema`/`review_score`; ethos fail-closed; **anti-anchoring** (revisor nunca recebe CoT do implementador, com sanitização de `<thought>`/`<thinking>`).
5. **Isolamento configurável.** 7 backends + `docker_egress.py`.
6. **Superfície de interface.** 6 interfaces distintas + ~20 plataformas de mensageria.
7. **Memória.** 9 providers plugáveis + curator + FTS + compressão.
8. **Segurança.** 19 arquivos de approval/guard, incluindo `self_repo_guard.py`, `plugin_guard.py`, `tirith_security.py`, `browser_tool_eval_policy.py`.
9. **Catálogo de plugins curado.** 101 entradas com SHA pinado, gate humano, proibição de auto-update.

---

## 5. Pontos fracos do HAOS

1. **Escala não governada.** 836.594 LOC de fonte com **77 arquivos acima de 2.000 linhas**. O próprio `AGENTS.md` define o limite em ~2.000 LOC/arquivo — o repo viola a própria regra 77 vezes. `apps/desktop/electron/main.ts` = 18.528 linhas.
2. **Custo de teste insustentável.** Razão teste/fonte = **1,38** (A0 = 0,44). `test_durations.json` com 312 KB no repo é sintoma do tempo de ciclo.
3. **Comunidade própria ~zero.** Repo privado (GitHub API → 404). Sem estrelas, sem contribuidores externos ao core. O catálogo de plugins é herdado do upstream.
4. **Docs fragmentadas e bilíngues.** 535 `.md` entre `docs/` e `website/docs/`; `docs/haos/ARCHITECTURE.md` em português, restante em inglês. Sem caminho de onboarding de usuário final.
5. **Onboarding pesado.** `setup-hermes.sh` + `distro/haos-linux/build-iso.sh` (Debian live-build). Comparar com `docker run -p 80:80 -v a0_usr:/a0/usr agent0ai/agent-zero`.
6. **Sem gate de tipagem Python.** Não há config de mypy/ruff estrita aplicada ao conjunto; a proteção vem de scripts de lint pontuais.

**INFERÊNCIA:** os itens 1+2 são o risco dominante de longo prazo — a taxa de regressão tende a crescer mais rápido que a cobertura de teste consegue acompanhar.

---

## 6. Pontos fortes do Agent Zero

1. **Desktop Linux real no container.** `plugins/_desktop/` (4.507 LOC): XFCE + noVNC no Canvas. Dirige Blender, LibreOffice, qualquer GUI sem API. O `computer_use` do HAOS (4.020 LOC) é **macOS/AX-based** (`bundle IDs`, `AXPopUpButton` em `tools/computer_use/schema.py`) — controla o host do usuário, não um desktop isolado.
2. **Browser com anotação DOM.** `plugins/_browser/` (7.616 LOC). Modo Annotate: clicar elemento → *change / inspect / lift / comment*. "Lift" captura componente de site terceiro para reimplementação. UX original.
3. **Cowork em documentos ao vivo.** Editor Markdown no Canvas com edição simultânea humano+agente; Writer/Calc/Impress (ODT/ODS/ODP).
4. **Pipeline completo de plugins.** `_plugin_installer` (ZIP/Git/Index) + `_plugin_validator` + `_plugin_scan` (scanner de segurança guiado por LLM).
5. **31 pontos de extensão por ciclo de vida** + layout de precisão `_functions/<module>/<qualname>/<start|end>/` (ex.: `Agent/hist_add_ai_response/end`), permitindo interceptar uma função específica sem tocar no core.
6. **`*.py.dox.md` — documentação obrigatória por arquivo.** Todo `tools/*.py` exige `.dox.md` irmão com propósito, argumentos, saída, `break_loop`, side effects e verificação.
7. **`security-review/LEDGER.md`.** 2 achados (WA-001 path traversal no WhatsApp; TG-001 bypass de auth no webhook Telegram) documentados com baseline, commit de introdução, usuários afetados, **fronteira de confiança**, PoC reproduzido (`POC_CONFIRMED`), remediação, comandos de verificação e — decisivo — seção **"Verification and limits"** declarando explicitamente o que **não** foi testado.
8. **Time Travel com UI.** `plugins/_time_travel/` (2.255 LOC): snapshot/diff/preview/travel/revert por workspace, painel no Canvas.
9. **A0 CLI Connector.** Bridge terminal-native ligando a instância Docker a repos no host, com escopo explícito de Read+Write/RCE.
10. **Foco.** 116k LOC de fonte entregando produto coerente; `agent.py` = 1.613 linhas; zero abstração especulativa.
11. **Simetria de orquestração.** `_orchestrator` lista **Hermes Agent** entre os agentes externos que o A0 sabe dirigir — hoje o A0 pode orquestrar o HAOS, e não o contrário.

---

## 7. Pontos fracos do Agent Zero

1. **Nenhum CI de teste.** 2 workflows (`docker-publish.yml`, `close-inactive.yml`). `grep -n "pytest" .github/workflows/docker-publish.yml` → 0 ocorrências. 1.516 testes existem sem nada que garanta que passem.
2. **Sem `pyproject.toml`, sem lint, sem typecheck.** 4.067 de 8.500 `def` têm anotação de retorno (~48%).
3. **God files.** `tests/test_browser_agent_regressions.py` = **5.038** LOC (um único arquivo de teste); `plugins/_browser/helpers/runtime.py` = 3.148; `plugins/_desktop/helpers/desktop_session.py` = 2.600; `helpers/litellm_transport.py` = 2.196.
4. **Imagem Docker pesada.** `unstructured[all-docs]`, `openai-whisper`, `kokoro`, `sentence-transformers`, `faiss-cpu`, `patchright`, LibreOffice, XFCE.
5. **18 dependências sem upper bound** em `requirements.txt` (`boto3>=1.35.0`, `uvicorn>=0.38.0`, `cryptography>=46.0.0`, …), contra a política de bounds do HAOS.
6. **Poucas plataformas.** Telegram, WhatsApp, email.
7. **Memória é plugin opcional.** `plugins/AGENTS.md`: *"do not assume it is enabled outside this plugin"*.
8. **Arquitetura documentada fora do repo.** `docs/developer/architecture.md`: *"Agent Zero architecture is now documented in DeepWiki… This local page intentionally stays short so the repository does not maintain a second, stale architecture manual."*
9. **Frontend Alpine.js** (34k LOC JS, 109 HTML), sem tipos.

---

## 8. O que incorporar — priorizado

### P0 — alto valor, encaixe direto

**8.1 `*.py.dox.md` — documentação obrigatória por arquivo**
*Problema:* 836k LOC, 77 god files; a regra "leia o `AGENTS.md` da área" não escala para 2.253 arquivos.
*Encaixe:* `scripts/check_dox_coverage.py` + job em `lint.yml`, começando por `tools/` e `hermes/platform/`. Zero mudança de comportamento.

**8.2 Formato do `security-review/LEDGER.md`**
*Problema:* existe `SECURITY.md` (política) mas não um ledger de achados com baseline, PoC reproduzido e a seção **"Verification and limits"**.
*Encaixe:* `docs/security/LEDGER.md` — o diretório `docs/security/` já existe.

**8.3 Scanner de segurança de plugins guiado por LLM**
*Problema:* `plugin-catalog/` tem 101 entradas de terceiros com SHA pinado, mas não há scan semântico do conteúdo — o SHA garante imutabilidade, não segurança.
*Encaixe:* plugin novo consumindo o catálogo existente. Rung 4 da Footprint Ladder; não toca o core.

### P1 — diferenciais de produto ausentes

**8.4 Painel visual de Time Travel sobre o `checkpoint_manager`**
*Problema:* o motor existe (`tools/checkpoint_manager.py`, 1.270 LOC, shadow git store) e os comandos também (`/rollback <N>`, `/rollback diff <N>`, `/rollback <N> <file>`). Falta a UI: `SystemPage.tsx` só mostra tamanho e botão *prune* — é gestão de armazenamento, não exploração de histórico.
*Encaixe:* router em `hermes_cli/web_routers/` + página no dashboard sobre `history list / diff / preview / revert`. Trabalho de superfície apenas.

**8.5 Modo de anotação DOM no browser**
*Problema:* 23 arquivos de browser (8.793 LOC) — base técnica mais profunda que a do A0 — sem o overlay de anotação.
*Encaixe:* `tools/browser_tool_annotate.py` + injeção de overlay via CDP, alavancando a infra existente.

**8.6 Desktop Linux isolado no container (XFCE + noVNC)**
*Problema:* `computer_use` controla o host do usuário — útil mas arriscado.
*Encaixe:* variante de imagem sobre o `Dockerfile`/`docker/` (s6-rc.d já presente) + backend novo; a ABC em `tools/computer_use/backend.py` já prevê *"future Linux/Windows"*.

### P2 — custo baixo

**8.7 Comentário CVE nas dependências.** A0: `litellm==1.88.1  # CVE-2026-42271 fix: patched floor is 1.83.7`. O HAOS tem a política de bounds mas não o registro do motivo.
**8.8 Indicador de janela de contexto no composer.** `plugins/_context_window/` do A0.
**8.9 Modal "What's New" version-gated.** `_whats_new` do A0.
**8.10 `hermes import --from agent-zero`.** `_migrate_agents` do A0 importa chats/projetos/memórias/skills de outros harnesses; hoje a simetria não existe.

---

## 9. O que NÃO incorporar

- **Extensão por monkey-patch de função (`@extensible` + `_functions/<qualname>/<start|end>`).** Elegante em 116k LOC; em 836k LOC com cache de prompt como invariante sagrado, hooks em funções arbitrárias do core são exatamente a mutação mid-conversation que o `AGENTS.md` proíbe. A superfície atual (ABCs + `gateway/builtin_hooks/`) é a correta.
- **Arquitetura documentada apenas em wiki externo.** A tabela de roteamento do `AGENTS.md` é superior.
- **Delegação simplificada.** Seria regressão frente ao `SpawnResolver`/`CompositeAcceptanceEngine`.
- **Pins exatos sem upper bound.** A política do HAOS é melhor; adote apenas o hábito do comentário CVE.

---

## 10. Conclusão

**HAOS 83,5 / Agent Zero 71,5** (pesos na §3; JULGAMENTO, não medição).

Filosofias opostas, não concorrentes diretos:

- **Agent Zero é um produto.** 116k LOC, foco, UX de workbench (desktop Linux, anotação DOM, cowork em documentos), 19,2k stars, 66 contribuidores, ecossistema de 100+ plugins. Profundidade de engenharia menor: sem CI de teste, sem lint, sem typecheck, 30 arquivos acima de 1.000 LOC.
- **HAOS é uma plataforma.** 836k LOC, 44k testes com harness verificado, 37 workflows, 7 backends de execução, 9 providers de memória, orquestração multi-agente com garantias formais. Profundidade muito maior; comunidade e manutenibilidade são os pontos frágeis.

**O maior gap do A0 é rigor de engenharia** (3,0 vs 9,5 em testes/CI): 1.516 testes que nenhum pipeline executa.
**O maior gap do HAOS é distribuição e escala governada** — e o de escala é acionável internamente, enquanto o de distribuição não é.

Os itens P0 (§8.1–8.3) são o caminho de maior retorno por unidade de esforço: nenhum altera comportamento, todos reduzem risco num codebase de 836k LOC.

---

## 11. Limites deste relatório

- **UNVERIFIED:** a suíte do Agent Zero não foi executada — as dependências não estão instaladas neste ambiente (§1.3). Não afirmo que passa nem que falha.
- **UNVERIFIED:** apenas 6 testes do HAOS foram executados como amostra. Os 44.463 são contagem de `def test_*`, não resultado de execução.
- **NÃO MEDIDO:** nenhum benchmark de performance, custo de token, latência ou qualidade de tarefa. Nada neste relatório é medição de desempenho.
- **JULGAMENTO:** todas as notas 0–100 e os pesos da §3 são subjetivos. A conclusão é sensível a eles (§3, "Sensibilidade dos pesos").
- **NÃO INSPECIONADO:** as 101 entradas do catálogo foram validadas por estrutura (presença de `repo`/SHA/tier), não uma a uma quanto a conteúdo ou funcionamento.
- **PARCIAL:** as notas 1–8 e 12 baseiam-se em leitura de código, não em execução dos subsistemas correspondentes.
