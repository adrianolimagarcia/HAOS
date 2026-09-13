# HAOS Neuromorphic — user theme (Hermes Agent Dashboard)

Tema de usuário para o **dashboard oficial do agente** (mesma UI que tem toda a
configuração: providers, keys, webhooks, cron, skills…). É a "repaginada"
visual clean/tecnológica pedida — o conteúdo/estilo continua o do dashboard,
só o visual muda (paleta vidro fosco profundo, acentos ciano/violeta,
tipografia tech Space Grotesk/Inter/JetBrains Mono, bordas suaves).

## Como instalar (seam oficial — sem tocar no código do dashboard)

```bash
# 1. Copiar o tema para o diretório de user themes do Hermes
cp plugins/haos/dashboard-themes/haos-neuro.yaml ~/.hermes/dashboard-themes/

# 2. Ativar no config.yaml (chave dashboard.theme)
#    (ou pela UI: Settings > Appearance > tema "HAOS Neuromorphic")
```

O `/api/dashboard/themes` lista o tema novo com a `definition` normalizada e o
frontend aplica palette/typography/layout/customCSS quando ele é o ativo.
Backend do tema: `hermes_cli/web_server_dashboard.py`
(`_normalise_theme_definition`, `_discover_user_themes`); frontend:
`web/src/themes/`.

## Campos usados por este tema

| Campo | Efeito |
| :-- | :-- |
| `palette` | `background` (base escura), `midground` (texto), `warmGlow`/`noiseOpacity` |
| `typography` | fontes (Sans/Mono + `fontUrl` Google Fonts), tamanho, entrelinha, espaçamento |
| `layout` | raio de borda `1rem`, densidade `comfortable` |
| `colorOverrides` | tokens shadcn do shell (card, primary, accent, border, ring…) |
| `componentStyles` | custom props `--component-<bucket>-<prop>` consumidas pelo shell |
| `customCSS` | glow radial de fundo, caret, scrollbar — escopo global, cap 32 KiB |

## Acesso pela rede (gate de auth)

O dashboard **recusa** bind não-loopback sem auth provider (por design:
`should_require_auth`). O caminho suportado (usado nesta máquina):

```yaml
# ~/.hermes/config.yaml
dashboard:
  theme: haos-neuro
  basic_auth:
    username: haos
    password_hash: "<hash scrypt>"
    secret: "<hex random ≥ 16 bytes>"
```

Gerar o hash:

```bash
python -c "from plugins.dashboard_auth.basic import hash_password; \
print(hash_password('sua-senha'))"
```

Depois suba com `--host 0.0.0.0`; o login em `/auth/password-login`
(provider `basic`) seta cookie e libera `/api/*`. Nesta máquina as
credenciais ficam em `/root/.hermes/haos/dashboard-access.txt` (600).
**Rotacione a senha** trocando `dashboard.basic_auth` e reiniciando o dashboard.
