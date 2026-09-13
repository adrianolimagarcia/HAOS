# Auditoria: GasTown Roles & Team (HAOS)

## 1. Escopo

Auditoria *read-only* do modelo de papéis/team do **GasTown** (Go, referência em
`TEMP/references/gastown`) para port **clean-room** em HAOS — *conceito entra,
código Go não é copiado*; stdlib-only; sem runtime externo no processo
(INTEGRATIONS.md §3, linha da row GasTown). Áreas auditadas: `internal/config/roles.go`
+ `internal/config/roles/*.toml`, `internal/templates/roles/*.md.tmpl`, watchdogs
(`internal/witness|deacon|mayor|refinery`), `gt-model-eval/` e docs (`README.md`,
`docs/design/escalation.md`, `docs/gas-city/crew-specialization-design.md`).

## 2. Achados-Chave

### 2.1 Papéis canônicos (fonte: `roles.go:108-121`; AllRoles = 7)

| Papel | Escopo/cardinal. | Verbo (responsabilidade) | Modelo | Consome → Produz |
|---|---|---|---|---|
| **mayor** | town, 1 | Coordenador global: decompõe objetivos, despacha (`gt sling`), escalações, decisões estratégicas; quase nunca implementa | orquestrador (alto) | intenção/convoy/mail → convoys, sling, nudges |
| **deacon** | town, 1 | Supervisor cross-rig: patrulha contínua, watchdog de witnesses/refineries, despacha Dogs (patrulha de 25 passos) | prescritivo (downgrade p/ barato) | health/heartbeats → patrol reports, dog dispatch, escalações |
| **dog** | town, n | Worker de infra one-shot sob Deacon; WAIT-FOR-SLUNG → executa → `DOG_DONE` | prescritivo | hook/mail → evidência de tarefa |
| **witness** | rig, 1 | Oversight por rig: monitora polecats, nudge, cleanup, **não implementa**; em `POLECAT_DONE`+MR emite `MERGE_READY` p/ refinery | gate de revisão | estados de polecat → receipts, nudges, MERGE_READY, escalações |
| **refinery** | rig, 1 | Fila de merge: rebase sequencial → gates de verificação (testes) → merge ff-only; falha volta p/ witness (nunca p/ polecat) | gate de revisão | MERGE_READY + MQ → merges, MERGE_FAILED, fecho de beads |
| **polecat** | rig, n | Worker transitório: implementa 1 tarefa no worktree, persiste achados, `gt done`; **não fecha a própria issue** | implementador (barato) | bead hook/formula → código, MR |
| **crew** | rig, n | Deputy-mayor persistente guiado por humano ("fat" work, despacha polecats) | orquestrador | assignment → código, dispatches, handoffs |

Citações-chave: definição por TOML + merge de override town→rig (`roles.go:134-176`);
mayor (`mayor.md.tmpl:113,184-192`), witness ("não implementa" `witness.md.tmpl:58-69`;
`handlers.go:139-144,336-345`), refinery (`refinery.md.tmpl:40,120,193-197`), polecat
(`polecat.md.tmpl:137,219`), crew (`crew.md.tmpl:55-67`; Overseer = humano, `:125`).

### 2.2 Modelo por função NÃO é hardcoded

Todos os papéis iniciam `exec claude` (TOMLs). A seleção de modelo é superfície de
config/CLI: runtime por rig (`settings/config.json`), aliases com `--model`
(README:526-530), `--agent` por spawn (README:499-501), `model=` por step de molécula
+ `GT_DEFAULT_MODEL` (`model-aware-molecules.md:322,394`). `gt-model-eval` compara
Opus/Sonnet/Haiku (promptfooconfig.yaml:18-31) para justificar **downgrade** de papéis
prescritivos de patrulha (deacon/witness/dogs) para tier barato (README:3,7). → o HAOS
deve tornar essa tabela **explícita e validável**.

### 2.3 Topologia de time e orquestração

- **Composição**: town = mayor + deacon (+ dogs); rig = witness + refinery (+ polecats
  transitórios, crew opcional). `gt up` sobe daemon/deacon/mayor + witness/refinery dos
  rigs acordados; polecats **não** sobem (transitórios) (`cmd/up.go:127-156`). Rigs
  dormem por padrão (dock/undock).
- **Pipeline de artefato** (DAG): mayor → polecat (implementa) → witness (gate) →
  refinery (gate+merge). Handoffs: `gt done` → POLECAT_DONE → witness MERGE_READY →
  refinery merge → MERGED → cleanup witness + fecho do bead (refinery.md.tmpl:60-68).
- **Concorrência**: fan-out paralelo de polecats com teto (`scheduler.max_polecats`);
  refinery **serial** (Bors, bisect; "polecats nunca empurram direto pra main",
  README:653-661).
- **Escalação**: agente → Deacon → Mayor → Overseer(hu.) por severidade
  (`escalation.md:13-31`); rota por categoria em `witness/protocol.go:664-673`.

## 3. Lacunas Acionáveis (à época da auditoria)

1. **Sem roster nomeado nem tabela de modelo-por-papel** — o GasTown deixa o modelo por
   função implícito em config; HAOS tinha só modelo-por-postura.
2. **`review_stages` declarados mas nunca executados em ordem** — TaskSpec declara
   `ReviewStage(posture/model_profile/independence)`, mas nada os orquestra; o gate de
   upstream não bloqueia o downstream de ficar `ready`.
3. **`TaskSpec.team_id` (spec.py:25) é passivo** — nenhum resolver/dispatcher consulta o
   time; `MessageEnvelope.team_id` idem.
4. **Sem postura para supervisor/refinery** — deacon (patrulha/watchdog) e refinery
   (gate de merge) não tinham equivalente entre as 6 posturas default.

## 4. Status de Implementação

| Achado | Arquivo / caminho | Status |
|---|---|---|
| Roster `TeamSpec`/`TeamRole` + `TeamResolver` (registra/resolve/valida) | `hermes/platform/execution/team.py:117-281` | Implementado |
| Tabelas canônicas (papéis/postura/modelo/lane/cardinalidade) | `team.py:35-101` (ROLE_*_MAP, COMPOSITION_RULES, DEFAULT_GATE_EDGES) | Implementado |
| Modelo por função + precedência task > role > tabela > preferred | `team.py:284-326` (`binding_for`) | Implementado |
| Posturas novas `supervisor` e `refinery` (+ `registered_ids`) | `hermes/platform/posture/specs.py:92,102` e `:33` | Implementado |
| Time default (town+rig completo) | `team.py:332-373` (`default_team`) | Implementado |
| Invariantes unittest (canonical roles, resolução de postura/perfil, composição, DAG, cardinalidade, precedência) | `tests/platform/execution/test_team.py` (9 testes) + `test_preferences_and_events.py:211` | Implementado/verde |
| Registro no índice de auditorias | `docs/haos/audits/README.md:25` | Este relatório |
| **Wiring de produção** (dispatcher/SpawnResolver consultando time; execução do DAG de gates) | `team.py` só é importado por testes hoje | **Lacuna aberta** |

## 5. Recomendações

1. **Conectar o time à resolução**: no `SpawnResolver`/dispatcher, resolver
   `TaskSpec.team_id` via `TeamResolver.binding_for` antes de montar o `AssignmentSpec`
   (postura/perfil/lane herdados; precedência já implementada em `binding_for`).
2. **Executar a ordem de gates**: orquestrar os `ReviewStage`s segundo o DAG — downstream
   só transita para `ready` após veredito do upstream (estado `review`/`request_review`
   do Kanban já existe; falta o orquestrador que consulta `team.gate_edges`).
3. **Não duplicar o kernel**: fan-out = multiplicity `many` + workers de claim/lane (K1);
   serialização do refinery = gate, não um supervisor novo.
4. **Teste de contrato E2E** do fluxo team → card → gate → done (relação, não snapshot;
   usar `scripts/run_tests.sh tests/platform/`).
5. (Opcional) espelhar severidade/escalação do GasTown em `risk_level` + stage
   `security-reviewer` para tarefas high/critical.
