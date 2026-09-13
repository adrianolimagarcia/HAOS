# Sincronização com o upstream (NousResearch/hermes-agent)

Plano e números medidos em 12/09/2026, **executado parcialmente em 19/09/2026** (ver
"Execução desta sessão" abaixo). O objetivo do documento é que a próxima sessão não
precise redescobrir o terreno, e que a execução parcial fique registrada com evidência.

## Fatos medidos

- **As histórias não têm ancestral comum.** O repo do fork nasceu de um import da
  árvore do upstream (raiz `4c6f7b6f20`, 09/09/2026) — não é um clone com histórico
  compartilhado. `git merge-base HEAD upstream/main` não devolve nada e
  `git merge-base --is-ancestor HEAD upstream/main` é falso. Consequência: `git merge`
  puro é recusado ("refusing to merge unrelated histories") e um graft por
  `git replace --graft` **não é honrado pelo merge** (testado: o merge continuou
  recusando e `merge-base` ficou vazio).
- **Remote**: `upstream` = https://github.com/NousResearch/hermes-agent.git
  (`git fetch --no-tags upstream main`, ~4 min). `upstream/main` em 19/09 = `b9271bcb34`.
- **Base de import identificada**: `5cffc57bc6` (07/09/2026) — pontuando cada commit
  do upstream na janela 05–08/09 por quantos arquivos têm blob idêntico à raiz do
  fork, o melhor candidato casa **11853 de 12428 arquivos (95,4%)**. Dois commits
  empatam no topo (`5cffc57bc6`, `40da71dbb4`), equivalentes para este fim.
- **Delta do upstream desde a base (12/09)**: 455 commits, 1179 arquivos, +64109/−7876.
- **Censo de conflito** (`git merge-tree --write-tree --merge-base=5cffc57bc6 HEAD upstream/main`,
  exit 1, 36 linhas `CONFLICT`): **36 arquivos**. Distribuição: `hermes_cli/` 19,
  `tools/` 3, `tests/` 3, `cron/` 3, `agent/` 3, `tui_gateway/` 2, `gateway/` 2,
  `toolsets.py` 1. A concentração em `hermes_cli/` é esperada: é onde vive a marca
  (hermes→haos).

Lista completa dos 36 (medição de 12/09):

```
agent/auxiliary_client.py            hermes_cli/runtime_provider.py
agent/chat_completion_helpers.py     hermes_cli/session_export_md.py
agent/turn_explainers.py             hermes_cli/setup.py
cron/jobs.py                         hermes_cli/subcommands/debug.py
cron/notepad.py                      hermes_cli/subcommands/plugins.py
cron/scheduler.py                    hermes_cli/tips.py
gateway/run_notifications.py         hermes_cli/update_cmd_fleet.py
gateway/run_turn.py                  tests/agent/test_compression_attempt_lifecycle.py
hermes_cli/auth_commands.py         tests/hermes_cli/test_cmd_update.py
hermes_cli/auth.py                   tests/hermes_cli/test_update_yes_flag.py
hermes_cli/backup.py                 tools/bot_mode_dm.py
hermes_cli/banner.py                 tools/computer_use/schema.py
hermes_cli/commands_platforms.py     tools/tts_tool.py
hermes_cli/config.py                 toolsets.py
hermes_cli/debug.py                  tui_gateway/methods_session.py
hermes_cli/doctor_state.py           tui_gateway/server.py
hermes_cli/__init__.py               hermes_cli/_parser.py
hermes_cli/inventory.py              hermes_cli/plugins_cmd.py
```

## Execução desta sessão (19/09/2026) — alinhamento de conteúdo

### Fatia da reconciliação (medido nesta sessão, árvores atuais)

| Classe | Contagem | Destino |
|---|---|---|
| Só-nosso (ausentes no upstream: tools `haos_*`, `serve_haos.sh`, skills `haos-*`, camada platform) | 195 | **permanecem locais** |
| Só-upstream (presentes nas duas, divergentes, nunca tocados) | 635 | core revertido (abaixo); conteúdo mantido |
| Ambos (tocamos E o upstream mudou) | 672 | **lista de revisão por arquivo** (não sobrescritos) |
| Novos do upstream (ausentes do nosso tree) | 232 | **não adotados** (decisão de arquitetura) |

### Por que o core NÃO foi adotado (evidência de cadeia)

Adotar os 635 quebrou a importabilidade por cadeia: `model_tools.py` novo importa
`hermes_state_ids` (módulo novo, dos 232) que exige `openrouter_variant_base` do
`hermes_constants` **novo** — e `hermes_constants.py` é arquivo nosso com fixes
(classe "ambos"). Evidência: `ModuleNotFoundError: hermes_state_ids` → após adotar,
`ImportError: openrouter_variant_base` → próxima dependência. **Conclusão:** adotar o
core do upstream arrasta a arquitetura nova dele; é um port, não um sync — reforça o
"revisar, não aplicar cego" do plano original (a estratégia graft+replay resolve isso
por construção: o diff do raiz do fork é reaplicado e revisado commit a commit).

### Entregue: 140 arquivos de conteúdo adotados (branch `sync-upstream`)

- `optional-skills/` (creative, software-development, etc.) e `skills/`: evolução do
  conteúdo de skills do upstream.
- `evals/`: harnesses novos/evoluídos (desktop_bug_campaign, tool_search,
  toolperf_abeval, token_accounting, provider_wire/fallback, gateway_failure_ownership,
  desktop_mcp_oauth, codex_echo, browser_use...).
- `docs/`: evolução de conteúdo.
- Colisões resolvidas: `reddit-reading` e `rss-feeds` existiam em `skills/` E
  `optional-skills/` após a adoção (violação `unique_names` do auditor P3) —
  colapsadas para a colocação do upstream (apenas `optional-skills/`).
- Árbitro: suíte v9 sobre o tree final do sync (ver certificação abaixo). Auditor P3
  `PASS` (213 skills, 0 violações). Smoke de import do core: OK.

### Commits (branch `sync-upstream`, sem push)

```
3d7f3bcd04 sync(upstream): fase 2c - remove os 90 novos do core que restavam
ba97e42c10 sync(upstream): fase 2b - remove arremedos do core que o rm anterior nao pegou
b74be716f7 sync(upstream): fase 2 - reverte o core entrelacado, mantem apenas o conteudo
987606b464 sync(upstream): fase 1b - adota modulos novos exigidos + colapsa duplicatas
d3280faae4 sync(upstream): fase 1 - adota 635 arquivos que o upstream evoluiu (revertidos no core)
```

## Procedimento (estratégia escolhida: graft + replay) — para o sync TOTAL

1. `git checkout -b haos-upstream-sync upstream/main`.
2. Aplicar as mudanças do **commit raiz do fork** (o diff dele contra a base):
   `git diff 5cffc57bc6 4c6f7b6f20 > /tmp/fork-root.patch` e `git apply -3`. **Revisar,
   não aplicar cego**: esse diff carrega tanto as edições de marca do fork quanto o
   que o fork removeu do upstream no import. Reimpor a remoção é o que o fork quer,
   mas cada remoção precisa ser olhada. Commit único: "graft: fork root onto upstream".
3. Replay dos commits do fork (era 79 em 12/09; hoje são 143):
   `git rebase --onto haos-upstream-sync 4c6f7b6f20 haos-standalone`.
   Resolver os conflitos da lista acima (remediir: upstream avançou desde `cbe9e5b294`).
4. Portões de qualidade antes de qualquer coisa tocar `main`:
   `scripts/run_tests.sh` (suíte completa, não só os diretórios tocados) e E2E no
   appliance: `haos doctor`, cron agendado (`source=builtin`), DNS (DoT/DoH), gateway.
5. Só então `git branch -f main haos-upstream-sync` + push em lockstep.

## Riscos e ressalvas

- O censo de 36 é de um merge 3-way. Um replay commit a commit pode tocar o mesmo
  arquivo mais de uma vez — o que ele mede é a superfície de arquivos, não o esforço.
- `cron/scheduler.py` conflita por dois motivos independentes: a marca do fork e o
  commit do upstream `c17629a0a2 fix(cron): scope restart-safe worker environment`,
  que **não** substitui a correção local do scope (`cron.restart_safe_scope`,
  commit `6f5ee79af4`).
- O appliance roda um import independente em `/opt/haos` (ver DEV_WORKFLOW_VM.md);
  deploy continua sendo por arquivo.
- Rollback: o trabalho vive em branch dedicada; `main` e `haos-standalone` não são
  tocados até o passo 5. Nenhum passo deste plano mexe na ISO.

## Pendência futura (registrada)

- **Sync total (graft + replay)**: aguarda sessão dedicada com remedição das 36
  superfícies de conflito contra o upstream atual.
- **672 arquivos classe "ambos"**: revisão por arquivo (nossa mudança + upstream).
- **232 módulos novos do upstream**: entraram junto com o port do core (fase futura).
- **ISO**: bloqueado por ordem do dono — será o último passo, após todas as pendências,
  com autorização explícita. (Registrado em 19/09/2026; não criar.)