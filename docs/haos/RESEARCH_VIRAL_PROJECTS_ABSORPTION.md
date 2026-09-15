# Absorção dos 5 projetos virais (Medium / Coding Nexus) — decisões

Fonte: artigo "5 Viral GitHub Projects That Are Changing How Developers Build With AI"
(`medium.com/coding-nexus`, paywall). O texto foi fornecido manualmente; o fetch autenticado
continua bloqueado (ver "Bloqueio do Medium" abaixo).

Este documento registra **o que foi absorvido, o que foi rejeitado e por quê** — para que a
decisão não seja refeita nem invertida sem novo argumento.

## Os 5 projetos, como verificados

Números de estrelas e licenças foram lidos do GitHub na data da verificação; **são um retrato,
não um contrato** — não os use como gate.

| projeto | licença | veredito |
|---|---|---|
| GitNexus | PolyForm Noncommercial | **rejeitado** como dependência; padrão avaliado |
| chrome-devtools-mcp | Apache-2.0 | **rejeitado** (exige Chrome real; telemetria ligada por padrão) |
| FreeLLMAPI | MIT, "personal experimentation only" | **rejeitado** como dependência; padrão absorvido |
| Scrapling | BSD-3-Clause | **já integrado** (`scrapling-mcp` 0.4.15, `tools/lazy_deps.py`) |
| microsoft/hve-core | MIT (+ skills CC BY-SA 4.0) | **rejeitado** como dependência; padrão absorvido |

## O que foi implementado a partir disso

### 1. Captura de rede no browser (`browser_network_requests`)

**Motivo:** o HAOS não tinha **nenhuma** visão de rede (grep por `network`/`requests` em
`tools/browser_tool*.py` não retornava nada). O agente não consegue obter status HTTP nem
headers via JS — `performance.getEntriesByType('resource')` expõe timing, não status nem
headers. Terminal + arquivo **não** substituem; nenhum MCP cobre isso; e `browser_console` já
era core. Por isso subiu ao core (rung 6 da Footprint Ladder), não a plugin.

**O que a CLI já tinha:** o binário `agent-browser` já suportava `network route|unroute|requests|har`.
Faltava só o wrapper de tool — nenhuma capacidade nova foi inventada, apenas exposta.

**Contrato:**
- `clear=True` **lê antes de limpar** (`--clear` da CLI devolve zero requests; limpar primeiro
  perderia os dados). Este é o invariante central e está sob teste.
- `only_failures` filtra status ≥ 400 e mantém `requests_seen` visível, para o agente saber que
  houve omissão.
- `include_headers` é opt-in (headers são volumosos e carregam credenciais).
- Modo Camofox **não tem** comando de rede — devolve erro explícito em vez de lista vazia.

**Correção de segurança encontrada durante os testes.** O redator genérico
(`agent.redact.redact_sensitive_text`) reconhece formatos conhecidos (`sk-…`, `ghp_…`) e
atribuições com chave (`Authorization: Bearer sk-…`), mas **vaza**:

| valor | resultado |
|---|---|
| `Bearer eyJhbGci…` (JWT) | redigido |
| `Bearer <token opaco>` | **VAZA** |
| `Cookie: sessionid=…` | **VAZA** |
| `Set-Cookie: session=…; HttpOnly` | **VAZA** |
| `Basic <blob opaco>` | **VAZA** |

Como `Cookie`/`Set-Cookie` são exatamente o que uma view de rede captura, foi adicionada
redação **por nome de header** (`_SENSITIVE_HEADER_NAMES` + `_redact_header_values`), somada ao
redator genérico. Headers inócuos (`Content-Type`, `Server`, `cf-ray`) seguem intactos — o sinal
de depuração não foi destruído.

### 2. Tree-sitter no graphify (multi-linguagem)

**Motivo:** o `CodeSymbolGraph` era **só Python** (`if not fname.endswith(".py"): continue`).
Neste repo isso escondia ~3.234 arquivos `.ts`/`.tsx` — todo o dashboard, desktop e TUI.

**Decisão:** implementar o caminho Tree-sitter em vez de adotar GitNexus (licença
noncommercial, Node-only, sem caminho Python).

**Medição real** (`web/src`, 171 arquivos `.ts`/`.tsx`):

| | antes | depois |
|---|---|---|
| símbolos | 0 | 976 |
| arquivos mapeados | 0 | 125 |
| chamadas | 0 | 9.205 |
| tempo | — | 1,1 s |

Distribuição: 414 funções, 272 métodos, 229 interfaces, 48 types, 9 classes. Os 46 arquivos sem
símbolos são arquivos de i18n (`export default { … }` — objetos literais, corretamente sem
declarações). 1 arquivo com erro de parse (`web/src/pages/ModelsPage.tsx`, um `&` literal em
JSX na linha 1303) — a recuperação de erro do grammar preserva os símbolos do arquivo.

**Opcional por construção.** O pack é extra opt-in; sem ele o scanner continua Python-only e
**diz o motivo** em vez de silenciar. Ausência degrada cobertura, nunca correção.

**Achado de supply-chain.** O repo filtra pacotes por `exclude-newer` (corte
`2026-09-01T04:44:23Z`). `tree-sitter-language-pack==1.20.0` foi publicado em 2026-09-14 —
**depois** do corte, logo não é instalável. O pin válido mais recente é **1.15.8** (2026-08-23).
Não subir além do corte.

## Padrões absorvidos sem adotar a dependência

### Roteamento de provedores (FreeLLMAPI)

Padrão observado no projeto e já coberto em espírito pelo HAOS
(`agent/fallback_cooldown.py`, `agent/provider_registry.py`):

- **Thompson sampling** para escolher provedor, com **piso de exploração de 10%** — sem isso o
  roteador converge cedo num provedor que estava apenas com sorte.
- **Escada de cooldown** 90s → 2m → 10m → 1h → 1d: falha curta não deve custar caro; falha
  persistente deve.
- **`X-Routed-Via`** no response, para a rota ser auditável de fora.
- **Teto de ≤20 tentativas** por requisição — limite de explosão combinatória.

Absorvido como **referência de design**, não como código: o HAOS já tem o mecanismo; o que
interessa é o piso de exploração e a auditabilidade da rota.

### Metodologia RPI (microsoft/hve-core)

**Research → Plan → Implement**, com agentes separados por fase e restrições duras em cada uma.

O HAOS já pratica a separação (fases de turno em `agent/turn_*.py`, `evals/`). O valor aqui é a
**restrição dura por fase**: o agente de pesquisa não escreve código; o de plano não executa; o
de implementação não replaneja. Vale como critério de revisão, não como subsistema novo.

## Bloqueio do Medium (não resolvido, documentado)

O fetch autenticado devolve o paywall mesmo com `sid`/`uid`/`cf_clearance` válidos. A GraphQL
`query ViewerQuery { viewer { id name username } }` devolve `{"data":{"viewer":null}}`, e `/me`
faz 302 → `/m/signin`. Conclusão: o cookie existe, mas a sessão **não** é reconhecida como
assinante nesse caminho. O conteúdo foi obtido por fornecimento manual do texto — o bloqueio
permanece para uso automatizado.

## Não-objetivos

- Não adotar GitNexus, FreeLLMAPI, hve-core ou chrome-devtools-mcp como dependência.
- Não habilitar telemetria do chrome-devtools-mcp (usar `--no-usage-statistics
  --no-performance-crux` se algum dia for usado).
- Não criar subsistema de roteamento novo: o que existe é ajustável, não substituível.
