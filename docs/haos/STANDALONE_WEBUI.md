# HAOS Standalone WebUI — produto próprio do HAOS

Este documento descreve a **UI web standalone do HAOS**: uma interface web
própria, servida pelo próprio HAOS, sem depender de clientes externos
(hermes-webui, dashboard oficial do agente, desktop). Ela segue o espírito da
interface completa que o operador conhecia (chat + configurações), porém com o
escopo **honesto** do que o HAOS realmente é: um **Task Engine** sobre o Kanban
canônico — não uma réplica do cliente de chat do agente.

## 1. O que é e o que NÃO é

**É** a superfície de operação do engine HAOS v1.2:
- **Console de Missões:** cada mensagem vira um `TaskSpec` no Kanban canônico
  (`Task ≠ Run`), é despachado pela lane canônica e a UI mostra o ciclo de vida
  real (READY → RUNNING → DONE / falha classificada).
- **Taskboard:** total por coluna, cards recentes com worker/resultado, criação
  de cards e despacho imediato.
- **Scheduler:** CPM (caminho crítico) e PIP (prioridades herdadas) calculados
  deterministicamente sobre o grafo real de tarefas.
- **ConcurrencyGuard:** tetos globais/por-provedor/por-modelo ao vivo e tarefas
  admitidas no momento.
- **Ouroboros:** análise de falhas (shadow mode) → propostas pendentes →
  decisão human-in-the-loop com identidade obrigatória do operador.
- **Config Agente:** editor da configuração completa do Hermes — o MESMO
  `config.yaml` real (`HERMES_HOME/config.yaml`), agrupado por seção, com
  gravação por patch parcial via seam canônico (`save_config(merge_existing=True)`),
  backup datado em `<data_dir>/config-backups/` antes de cada escrita e
  bloqueio fail-closed em perfis gerenciados.
- **Terminal:** emulador de terminal interno (xterm.js + bridge PTY real no
  backend, shell interativo por sessão com cwd/env próprios) — estilo v1.
- **Eventos:** trilha append-only do EventStore (projeção fail-safe do Kanban).
- **Configurações do Engine:** limites de concorrência e comportamento do
  engine, persistidos em `settings.json` e aplicados **ao vivo** ao
  `ConcurrencyGuard`.

**NÃO é** um cliente de chat do agente (conversa livre com LLM com histórico,
skins, troca de modelos, etc.). O editor da aba Config Agente opera sobre o
`config.yaml` real do agente; a conversa agent-level em si permanece nas
superfícies agent-level (dashboard oficial / hermes-webui).

> Superfície do dashboard oficial (plugin HAOS): ver `docs/haos/DASHBOARD_PLUGIN.md` — o backend do plugin é montado pelo servidor do dashboard oficial (`hermes dashboard` e o headless `hermes serve`, mesmo servidor); o hermes-webui não monta backend de plugin.

## 2. Arquitetura

```
hermes/platform/webui/
├── settings.py      # settings.json persistente (data_dir) + apply AO VIVO no guard
├── agentconfig.py   # editor do config.yaml do AGENTE (seams canônicos + backup)
├── terminal.py      # bridge PTY real (shell interativo por sessão, stdlib)
├── standalone.py    # servidor HTTP stdlib (ThreadingHTTPServer) + rotas /api/*
└── static/index.html# UI single-file (tema HAOS, sem build step, vanilla JS)
```

Princípios preservados da plataforma:
- **stdlib-only em nível de módulo** (http.server, sqlite3, json, dataclasses).
- **Nenhuma segunda fonte de verdade:** `/api/state` é `dashboard_payload`
  (views reais) + `evolution_pending`/`history` (ledger real) + tail de eventos
  (EventStore real). Nunca fabrica números — vazio é vazio.
- **Fail-closed:** sem store/DB a UI mostra estado vazio; `evolution/decide`
  exige `approver` (nunca inventa identidade).
- **Persistência canônica:** DBs file-backed em `data_dir` (resolução:
  `$HAOS_DATA_DIR` → `$HAOS_HOME` → `~/.haos`): `kanban.db` (KanbanAdapter
  canônico + meta HAOS) e `events.db` (EventStore append-only), conectados por
  um `EventStoreSink`.

## 3. Como rodar

```bash
bin/haos web                       # default: 127.0.0.1:8788, data ~/.haos
bin/haos web --port 8788 --data-dir /caminho/do/data_dir
```

Acesse `http://127.0.0.1:8788/` localmente ou `http://<IP_TAILSCALE>:8788/` quando iniciado com `--host <IP_TAILSCALE>`. `/api/state` expõe o mesmo payload em JSON.

> **Sem autenticação (rede confiável בלבד).** Este servidor Python **não tem login**: quem alcança a porta alcança o terminal PTY, o editor do `config.yaml` e as tasks. O bind padrão é `127.0.0.1`; para uma rede Tailscale, use explicitamente um endereço IPv4 `100.64.0.0/10`. Endereços públicos, wildcard e outras redes são recusados. Use somente em uma tailnet controlada.

## 3.1 Acesso ao daemon Rust (`haos-edge server`) — autenticação

No appliance o WebUI é servido pelo daemon Rust (`haos-edge server`, unit
`haos-edge.service`, `127.0.0.1:8788`) e **todo o control plane** (terminal PTY,
tasks, SPA) exige sessão autenticada:

- **Definir/alterar a senha do operador** (nó, como usuário `haos`):
  ```bash
  HAOS_DATA_DIR=/var/lib/haos/edge haos-edge admin set-password
  ```
  Grava PBKDF2-HMAC-SHA256 (200k iterações, sal por deployment) em
  `$HAOS_DATA_DIR/webui.passwd` (0600). **Sem senha definida o login responde
  503 e o WebUI fica inacessível — fail-closed**, nunca aberto por padrão.
- **Login**: `GET /login` (página própria do daemon) → `POST /api/login` com
  `{"password": "...", "remember": true|false}`. Com `remember=true` o cookie
  `haos_session` (HttpOnly, SameSite=Strict, Path=/) recebe `Max-Age` de 30 dias
  — é o "salvar login neste dispositivo". **A senha nunca é persistida no
  navegador**, só o token de sessão (arquivo 0600 em `$HAOS_DATA_DIR/sessions/`).
- **Público**: `/health`, `/static`, `/login`, `/api/login`. Todo o resto
  responde `401 {"error":"Não autenticado"}` (`/api/*`) ou `302 → /login` (SPA).
- **Logout**: `POST /api/logout` invalida o token. Para invalidar **todas** as
  sessões: `rm -rf $HAOS_DATA_DIR/sessions`.
- **Bind do appliance**: `127.0.0.1` — acesso remoto por túnel SSH
  (`ssh -L 8788:127.0.0.1:8788 haos@<nó>`); o daemon **não faz TLS**, então
  expor na rede exige proxy reverso com TLS na frente.
- A ISO derivada da VM **não carrega** a senha nem as sessões de
  desenvolvimento (`iso-from-vm.sh` remove `webui.passwd`, `sessions/` e os
  locks; a validação do squashfs falha explicitamente se eles estiverem
  presentes).

## 4. Rotas da API

| Método | Rota | Efeito (real) |
| :--- | :--- | :--- |
| GET | `/` `/index.html` | UI standalone |
| GET | `/api/state` | payload completo das views + ouroboros + eventos + settings |
| POST | `/api/console` | cria `TaskSpec` da mensagem e despacha 1 tick em background |
| POST | `/api/tasks` | cria card com goal/prioridade (`requires_tasks` opcional p/ dependências) |
| POST | `/api/dispatch` | dispara `claim_tick(max_spawn)` da lane canônica |
| POST | `/api/evolution/analyze` | Ouroboros shadow → submete propostas ao ledger |
| POST | `/api/evolution/decide` | aprova/rejeita proposta (requer `approver`) |
| GET/POST | `/api/settings` | lê / persiste e aplica config do engine ao vivo |
| POST | `/api/settings/reset` | restaura defaults |
| GET | `/api/agent-settings` | resumo curto da config agent-level |
| GET | `/api/agent-config` | snapshot editável do `config.yaml` real por seção |
| POST | `/api/agent-config` | patch parcial (paths pontilhados) + backup antes de gravar |
| POST | `/api/terminal/start` | abre sessão shell interativa (PTY), devolve `session_id` |
| GET | `/api/terminal/<sid>/drain` | consome saída acumulada da sessão |
| POST | `/api/terminal/<sid>/input` | envia teclas/bytes ao shell |
| POST | `/api/terminal/<sid>/resize` | `TIOCSWINSZ` real (linhas/colunas) |
| POST | `/api/terminal/<sid>/kill` | encerra a sessão |
| GET | `/api/terminal` | lista sessões ativas |

## 5. Honestidade operacional (limitações conhecidas)

- A execução de um card depende da **lane canônica** configurada no ambiente.
  Nesta máquina as lanes padrão executam com resumo determinístico em workspace
  canônico **sem invocar LLM**; um card cuja lane exija provider/modelo sem
  configuração falha com categoria classificada — a UI mostra a falha, não finge
  sucesso.
- A conversa com o agente (LLM com histórico/configurações) permanece nas
  superfícies agent-level; integrar o agente real ao Console do standalone é o
  próximo marco (rota de execução com o kernel do agente por trás da lane).
- **Editor do config.yaml:** escreve no `config.yaml` real via seams canônicos,
  com backup datado antes de cada gravação. O processo do gateway/dashboard já
  em execução carrega config em memória: as mudanças valem para novas sessões e
  após reload; reinicie o serviço agent-level se precisar aplicar já. Perfis
  gerenciados são read-only (a UI avisa e bloqueia).
- **Terminal:** cada sessão roda um shell com cwd/env próprios e nunca muta o
  `os.environ` do processo servidor (precedente da v1). Sessões ociosas são
  encerradas pelo reaper após ~60s; há cap de sessões simultâneas (6) com
  evicção da mais antiga. Sem PDEATHSIG (o `PR_SET_PDEATHSIG` é por-thread e
  mataria o shell ao fim da request); a limpeza é pelo reaper + kill explícito.
- O terminal é um shell de verdade no host: quem tiver acesso à UI tem acesso a
  um shell. Rode a UI standalone atrás da sua rede confiável (aqui: Tailscale).
- **Aba Sistema (`GET /api/system-facts`):** levanta fatos REAIS do ambiente —
  todas as `HERMES_HOME` detectadas (agente da máquina e operador/dev podem
  conviver), config resumida por home (modelo padrão, provider, orquestrador de
  delegação, leaf, memória, terminal), conhecimento (vault Obsidian, GraphRAG
  index, `memories/`) e paths do engine. Nunca inventa: recurso ausente aparece
  como vazio honesto ("—"). `api_key` e chaves secretas são mascaradas por nome
  de chave antes de sair no payload. A troca de modelos (padrão / orquestrador /
  leaf) grava no `config.yaml` real via o mesmo seam do editor, com backup.
  Sugestões de modelos são apenas os ids em uso real nas configs detectadas
  (catálogo de provider custom só existe em runtime; campo é livre).
