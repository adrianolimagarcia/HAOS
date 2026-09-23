---
id: ADR-013
titulo: "haos-shell: MCP de acesso root exposto ao ChatGPT web via Secure MCP Tunnel"
status: "Construído e verificado; INATIVO"
data: 2026-09-19
decidido_por: "human:adriano"
autor: "adriano"
contexto: "exposição de MCP haos-shell via tunnel outbound-only para ChatGPT web"
causado_by:
  - "decisão do dono por Tier C (root irrestrito) via OpenAI Secure MCP Tunnel com mitigações de auditoria"
evidence:
  - "análise do produto openai/tunnel-client v0.0.14 e avaliação de tiers de isolamento A/B/C"
affects:
  - "/var/log/mcp-shell/audit.jsonl"
  - "mcp-shell-server"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:implantacao-mcp-tunnel-shell"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "request:chatgpt-tunnel-access"
  causado_by: "necessidade de acesso remoto interativo do ChatGPT web com auditoria rígida"
  affects:
    - "/var/log/mcp-shell/audit.jsonl"
  supersedes: []
  superseded_by: null
---

# ADR-013 — haos-shell: MCP de acesso root exposto ao ChatGPT web via Secure MCP Tunnel

**Status:** Construído e verificado; **INATIVO** (falta `tunnel_id` + runtime key reais)
**Data:** 2026-09-19
**Decisão do dono:** Tier C — shell root irrestrito. Escolhido explicitamente após apresentação do inventário de exposição e dos tiers A/B/C.

## Contexto

O dono perguntou se o `openai/tunnel-client` (v0.0.14) permitiria o ChatGPT web conectar a este servidor. A resposta é sim, com escopo estreito: o produto é um túnel **outbound-only** que conecta **um servidor MCP** privado a ChatGPT/Codex/Responses API/AgentKit. Não é proxy genérico de rede.

Apresentados três tiers:

- **A** — contido (`bwrap`, `/` read-only, dir de trabalho único, sem `/root`)
- **B** — allowlist de comandos
- **C** — root irrestrito

O dono escolheu **C**, com o custo declarado: todo o cofre (3,7 GB em `/root/.haos`, incluindo `secrets/`, `vault/`, `obsidian_vault/`, `cron/`, `/root/.ssh`, tokens A2A, token do bot Telegram, senha do RustDesk) passa a ser alcançável, e a saída de cada comando trafega pela infraestrutura da OpenAI e entra no contexto do modelo.

## Decisão

Construir o MCP `haos-shell` com acesso root irrestrito, exposto via Secure MCP Tunnel, **com as mitigações que não reduzem capacidade**:

1. Audit log append-only em `/var/log/mcp-shell/audit.jsonl` (`chattr +a`), registrando tool, argumentos, exit, duração, tamanho e digest SHA-256 truncado do resultado.
2. Output truncado em 200.000 chars por stream, para não estourar contexto do modelo nem o TTL do túnel.
3. Timeout por chamada (default 120 s, teto 900 s) com `killpg` do process group — sem processos órfãos.
4. Health listener em `127.0.0.1:8080` (default do produto), nunca `0.0.0.0`.
5. Unit systemd `haos-mcp-tunnel.service` — não `setsid nohup`, que morre no fim do turno do agente.

**Hardening deliberadamente ausente:** sem `ProtectSystem`, `PrivateTmp` ou `NoNewPrivileges` na unit. O MCP filho herda o sandbox da unit; endurecê-la quebraria o propósito do Tier C. Isso é intencional, não omissão.

## Alternativa descartada

`Telegram → Hermes → shell root` já entrega a mesma capacidade por um canal controlado pelo dono, **sem** trânsito pela OpenAI. O Tier C não adiciona capacidade; adiciona uma rota com superfície maior. O dono optou por ter também a rota do ChatGPT web.

## Ferramentas expostas

`bash`, `python`, `read_file`, `write_file`, `list_dir`, `ssh_exec` (malha).

## Arquitetura

```
ChatGPT web (Connector: Tunnel)
  → OpenAI tunnel service (api.openai.com /v1/tunnels/{id}/poll)
    → tunnel-client (outbound HTTPS, long-poll, 127.0.0.1:8080 health)
      → stdio → /usr/local/lib/haos-agent/venv/bin/python server.py
        → subprocess como root neste nó
```

## Pendência única

Criar o túnel em `platform.openai.com/settings/organization/tunnels`, gerar runtime key **Restricted** com *Tunnels Read + Use* (nunca a admin key), preencher `/root/.haos/mcp/shell/tunnel.env` (0600) e `systemctl enable --now haos-mcp-tunnel`. O túnel também precisa estar associado ao **workspace do ChatGPT**, senão não aparece no picker do Connector.

## Consequência aceita

Qualquer identidade daquele workspace ChatGPT com permissão *Tunnels Read + Use* tem, efetivamente, root neste nó. O audit log é o único registro forense; ele não previne nada, apenas permite RCA.
