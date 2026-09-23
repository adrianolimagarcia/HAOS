---
id: ADR-011
titulo: "Google Drive como unidade de disco do servidor (rclone) e a identidade OAuth única"
status: "Aceito"
data: 2026-09-17
decidido_por: "human:adriano"
autor: "adriano"
contexto: "montagem de Google Drive via rclone no host cachyos-x8664"
causado_by:
  - "adr:ADR-005"
  - "adr:ADR-008"
  - "demanda do dono para montagem de storage Google Drive como unidade de disco do servidor"
evidence:
  - "rclone não instalado e token OAuth revogado em /root/.haos/google_token.json"
  - "teste de refresh token retornando 400 invalid_grant"
affects:
  - "/root/.config/rclone/rclone.conf"
  - "systemd/rclone-mount"
  - "/root/.haos/google_token.json"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:montagem-google-drive-rclone"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "request:mount-google-drive"
  causado_by: "necessidade de storage em nuvem integrado ao filesystem local"
  affects:
    - "/root/.config/rclone/rclone.conf"
  supersedes: []
  superseded_by: null
---

# ADR-011 — Google Drive como unidade de disco do servidor (rclone) e a identidade OAuth única

**Status:** Aceito (decisão do dono, 2026-09-17)
**Data:** 2026-09-17
**ADR relacionadas:** ADR-008 (REGISTRY: estado, log, índice no grafo), ADR-005 (caminho canônico do vault)
**Escopo:** nó `cachyos-x8664`. Não toca a malha A2A nem o espaço de ADR do repositório (ADR-009).

## Contexto (medido 2026-09-17)

Pedido do dono: "monte o google drive como uma unidade de disco do servidor".

Medições antes de decidir:

1. `rclone` **não estava instalado**; existe em `cachyos-extra-v3` (1.75.1-1.1). `fuse3` 3.18.3 e
   `/dev/fuse` já presentes. Nenhum `rclone.conf` no host, nenhum mount FUSE de nuvem.
2. O host **já tinha credenciais Google** do HAOS: `/root/.haos/google_client_secret.json`
   (projeto `gen-lang-client-0256516260`, OAuth client Desktop, `redirect_uris: ["http://localhost"]`)
   e `/root/.haos/google_token.json` com 8 escopos, **incluindo `auth/drive`**.
3. **Mas o token estava REVOGADO**: `POST https://oauth2.googleapis.com/token` com
   `grant_type=refresh_token` devolveu `400 invalid_grant: Token has been expired or revoked`.
   O `setup.py --check` do skill `google-workspace` confirmou `TOKEN_REVOKED`.
   ⇒ a integração Google do HAOS estava **quebrada** desde antes desta tarefa, independentemente do mount.
4. `setup.py --auth-url` exige consentimento interativo; o navegador controlado pelo agente (headless
   Chromium no próprio host) **não tem sessão Google** ⇒ o consentimento só podia ser feito pelo dono.

Havia duas identidades OAuth viáveis para o mount:

| Opção | Expiração do refresh token | Efeito colateral |
|---|---|---|
| Client embutido do **rclone** (app verificado pelo Google) | não expira em 7 dias | mostra "rclone" no consentimento; cota compartilhada com todos os usuários do rclone |
| **Projeto próprio** `gen-lang-client-0256516260` | **se o consent screen estiver em modo Testing, expira em ~7 dias** | reautorizar restaura de brinde Gmail/Calendar/Sheets/Docs do HAOS |

## Decisão

1. **Usar o client OAuth do próprio dono** (`gen-lang-client-0256516260`), escolha explícita dele, por
   restaurar na mesma autorização a integração Google do HAOS. O mount e o skill `google-workspace`
   passam a **compartilhar uma única identidade** e o mesmo arquivo de token.
2. **Não duplicar o token**: o `rclone.conf` é derivado de `google_token.json` (campos
   `access_token`/`refresh_token`/`expiry` remapeados para o formato do rclone) e o rclone renova o
   access token sozinho. Uma reautorização pelo `setup.py` basta para os dois consumidores.
3. **O mount é um serviço systemd, não um comando manual**: `rclone-gdrive.service`
   (`Type=notify`, `enabled`, `After/Wants=network-online.target`, `Restart=on-failure`), montando
   `gdrive:` em `/mnt/gdrive`, com `--vfs-cache-mode=writes` e `--allow-other`.
4. **O risco de expiração fica registrado, não escondido**: se o app estiver em modo *Testing*, o
   refresh token morre em ~7 dias e **mount e integração Google caem juntos** — o acoplamento é a
   consequência aceita da opção 1. O rollback é trocar o client do `rclone.conf` pelo client embutido
   do rclone (nova autorização, ~2 min).

## Consequências

- Um único ponto de falha para dois consumidores (mount + Gmail/Calendar do HAOS). Aceito
  conscientemente em troca de uma identidade só.
- O consentimento é o **único passo não automatizável**: `redirect_uri` é `http://localhost:1`, e o
  código só existe na barra de endereço do navegador do dono. O agente prepara a URL e troca o código.
- Sem `--allow-other` o mount seria inútil para o usuário `adriano` (HAOS roda como root). Verificado
  que o acesso não-root funciona.
- Não há garantia de que o mount sobreviva a um reboot testada de verdade: o host é onde a sessão do
  agente vive, então o reboot não foi exercitado (enable + `network-online` declarados, ciclo
  stop/start verificado).

## Verificação (executada)

`ls`/`df` no mount (5,0 TiB, 358 GiB usados, igual ao `rclone about`), escrita pelo FUSE com conteúdo
visível na Drive API, leitura de volta pelo mount **e** por download independente da API, exclusão
propagada à API, ciclo `systemctl stop/start` remontando, e leitura pelo usuário não-root `adriano`.
Detalhe operacional e pitfalls em `okf/licoes/` e na seção "Drive como filesystem" do skill
`google-workspace`.
