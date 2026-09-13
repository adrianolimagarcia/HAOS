# Rebranding HAOS (dashboard oficial do agente)

Tudo que a UI mostra como produto vira **HAOS**; referências visíveis à
**Nous Research** são removidas. Isto é **rebranding de superfície** — o
nome/identidade interna do fork continua `hermes` (pacotes, CLI `hermes`,
identificadores, header `X-Hermes-Session-Token`) para não quebrar
atualizações/plugins/`--insecure`-checks do upstream.

## O que foi trocado (fonte + dist)

- `web/index.html` → `<title>HAOS - Dashboard`
- `web/src/i18n/*.ts` (17 idiomas): `brand` "Hermes Agent"→"HAOS",
  `footer.org` "Nous Research"→"HAOS", `tweet_text` "…in Hermes Agent ☤"→"…in HAOS ☤",
  e demais valores de texto em que "Hermes" é o nome do produto → "HAOS"
  (comandos `hermes` minúsculos em dicas de CLI ficam intactos).
- `web/src/components/SidebarFooter.tsx`: link externo `nousresearch.com`
  removido (vira `<span>`); wordmark da sidebar em `App.tsx` → "HAOS";
  `ChannelsPage.tsx` bot_name default → "HAOS"; tema built-in
  `presets.ts` label "Hermes Teal" → "HAOS Teal" (o `name` é chave estável).
- **Deixado de propósito (funcional/não visível):** URLs funcionais
  `nousresearch.com` (docs do produto + portal de billing), identificadores e
  tokens de código, comentário em `index.css`.

## Como reconstruir o SPA (receita validada nesta máquina)

```bash
cd web
pnpm install            # uma vez (gera web/pnpm-lock.yaml)
node node_modules/vite/bin/vite.js build   # escreve ../hermes_cli/web_dist
```

Notas: `pnpm run/exec` falha nesta máquina no dep-check do pnpm 11
(ignored-builds); `tsc -b` falha num erro pré-existente não relacionado em
`vite.config.ts` — o `vite build` direto passa e é o caminho usado.
O dashboard serve `hermes_cli/web_dist` do disco (index.html `no-store`,
assets com hash de conteúdo), então a mudança vale no próximo reload.

## Verificação

```bash
# autenticado no dashboard em rede:
curl -s http://<host>:9119/ | grep -o '<title>[^<]*</title>'   # HAOS - Dashboard
# nos bundles servidos não deve sobrar "Hermes Agent"/"Nous Research"
```
