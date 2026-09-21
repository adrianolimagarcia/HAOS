# Plugin do dashboard oficial (superfície HAOS)

Este documento descreve a superfície HAOS **dentro do dashboard oficial do
agente**: o plugin `haos` declarado em `plugins/haos/dashboard/manifest.json`
(aba `/haos`, backend `api: plugin_api.py`). É distinta da UI web standalone do
HAOS (`docs/haos/STANDALONE_WEBUI.md`) e distinta do `hermes-webui`.

## 1. Qual servidor serve a superfície

O **servidor do dashboard oficial do agente** monta o `api: plugin_api.py`
declarado no manifest do plugin. São dois subcomandos do MESMO servidor:
`hermes dashboard` (com a UI) e `hermes serve` (headless — o backend que o
Desktop e clientes remotos usam). O mount das rotas de plugin é de nível de
módulo em `hermes_cli/web_server.py`, então **ambos** servem
`/api/plugins/haos/*`.

O `hermes-webui` — projeto de terceiro, `nesquena/hermes-webui`, que roda nesta
máquina na porta 8787 — **não monta backend de plugin**. O próprio código dele
declara:

```
plugin_api.py -- optional backend API (not used in WebUI MVP)
```

Consequência direta: as rotas `/api/plugins/haos/*` **nunca ficam vivas** no
hermes-webui. Subir o hermes-webui não publica esta superfície.

## 2. Não precisa instalar nada

O plugin é `bundled`. A descoberta varre
`<install tree>/plugins/*/dashboard/manifest.json` e a montagem do backend
libera a origem `bundled` a menos que o nome esteja em `plugins.disabled`.

Nesta máquina `plugins.disabled` não existe e o plugin é encontrado como
`source: bundled`. Não há passo de instalação, cópia ou registro manual.

## 3. Como subir

É o arranjo de `scripts/serve_all.sh`. O dist do frontend já vem empacotado em
`hermes_cli/web_dist/`, por isso `--skip-build` funciona e **não é preciso
npm/pnpm**:

```bash
hermes dashboard --host 127.0.0.1 --port 9119 --skip-build --no-open &
python scripts/serve_hermes_dashboard_proxy.py &   # 0.0.0.0:9191 -> 127.0.0.1:9119
```

Checagem:

```bash
hermes dashboard --status
```

## 4. Armadilha de autenticação (crítico)

Com o dashboard em loopback atrás do proxy, **o gate de auth não liga**:

- `should_require_dashboard_auth("127.0.0.1")` é `False` enquanto
  `dashboard.public_url` estiver vazio;
- o proxy é transparente — ele **não autentica**.

Resultado: o dashboard inteiro — editor de config, chaves de API, terminal —
exposto a qualquer cliente da rede. Existe um token exigido em `/api/*`, mas ele
é **entregue na própria página** que o cliente anônimo busca em `/`, o que na
prática equivale a não haver autenticação (é a terminologia do próprio upstream
para esse modo).

Antes de expor, escolha um dos dois caminhos:

1. preencher `dashboard.public_url` com a URL pública (isso liga o gate); ou
2. bindar direto num host não-loopback — bind não-loopback exige auth por design.

`--insecure` é **NO-OP**: o parâmetro é aceito e ignorado (só emite um aviso).

Nesta máquina o `dashboard.basic_auth` já está configurado no `config.yaml`; as
credenciais ficam em `/root/.haos/haos/dashboard-access.txt` (600).

## 5. Requisito de board (pré-requisito para a superfície fazer sentido)

O plugin monta o estado do engine a partir de `_haos_engine_dir()`, enquanto o
control plane HAOS usa `HAOS_DATA_DIR`.

Se os dois não apontarem para o **mesmo arquivo**, o console do plugin cria
missões num board que o control plane não lê: a superfície parece funcionar e
nada é despachado por ele. A divergência de caminhos é fato verificado; a
consequência é a leitura direta dela, não um experimento executado.

**O caminho recomendado é subir o dashboard com `HAOS_DATA_DIR` no ambiente.**
`_haos_engine_dir()` honra essa variável quando ela traz um `kanban.db` real, e
devolve esse diretório direto — o mesmo store do control plane, sem symlink
nenhum no meio.

Sem `HAOS_DATA_DIR`, ela usa `$HERMES_HOME/haos` com um symlink para o kanban
canônico do Hermes, e **repara symlink quebrado** (volume desmontado, alvo
removido) em vez de deixar o link pendurado — antes o `FileExistsError` era
engolido e o plugin lia um board inexistente em silêncio.

Verificação:

```bash
cd /usr/local/lib/haos-agent
HERMES_HOME=/root/.haos venv/bin/python -c "
from plugins.haos.dashboard.plugin_api import _haos_engine_dir
print(_haos_engine_dir().joinpath('kanban.db').resolve())"
```

O caminho impresso tem que ser o mesmo do `HAOS_DATA_DIR` do control plane.

Residual conhecido: `settings.json`, `agent_hierarchy.json`, `memory/` e
`config-backups/` continuam sendo os do engine dir do plugin. Unificar tudo
exigiria apontar `HERMES_HOME/haos` inteiro para o data dir do control plane.

`events.db` segue a mesma regra do kanban (aqui também apontado à mão): se esse
symlink se perder, o plugin **cria um `events.db` vazio em silêncio** — o painel
mostra eventos/Team Graph vazios sem erro nenhum. E se o volume montado
desaparecer, o symlink fica pendurado: `GET /state` devolve 500 enquanto
`/health` continua dizendo `available: true`.

## 6. A superfície carrega o default YOLO

As rotas `POST /console` e `POST /tasks/{id}/steer` (modos `queue` e
`interrupt`) do plugin despacham com `yolo_mode=True` via a política
`OPERATOR_YOLO_DEFAULT` (`hermes/platform/tasks/spec.py`): o worker é headless e
um prompt de aprovação ali não tem quem responda.

O taskboard manual (`POST /tasks`) mantém o portão.

## 7. Estado atual nesta máquina

A superfície está **dormente**. Não há processo do dashboard oficial nem do
proxy, e nada escuta em 9119/9191.

O código está no `main` e coberto por teste; falta subir o serviço.

Modelo de deploy: o `venv` do deploy é um install **editable** apontando para o
checkout, então o que o `hermes` executa é a árvore de trabalho — não a cópia em
`/usr/local/lib/haos-agent`. Editar o checkout já muda o que o dashboard subiria.
