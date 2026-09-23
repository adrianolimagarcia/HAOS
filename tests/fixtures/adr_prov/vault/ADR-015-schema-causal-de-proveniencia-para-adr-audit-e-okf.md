---
id: ADR-015
titulo: "Schema causal de proveniência para ADR, auditoria A2A e OKF"
status: "Aceito"
data: 2026-09-23
decidido_por: "human:adriano"
autor: "adriano / HAOS Subagent"
contexto: "governança e rastreabilidade de memória (PROV-O sem overhead)"
causado_by:
  - "adr:ADR-001"
  - "adr:ADR-005"
  - "adr:ADR-006"
  - "adr:ADR-009"
  - "necessidade de rastreabilidade causal em cadeia entre decisoes, auditoria e licoes aprendidas"
evidence:
  - "fragmentação de menções livres em prosa nos ADRs legados"
  - "contrato okf/contratos/proveniencia-prov-o-no-haos.md"
affects:
  - "obsidian_vault/adrs/*"
  - "okf/contratos/*"
  - "okf/licoes/*"
  - "a2a_audit.jsonl"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:fase-1-contratos-canonicos"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "contract:contrato-memoria-haos-nos-cachyos.md"
  causado_by: "necessidade de rastreabilidade causal em cadeia entre decisoes, auditoria e licoes aprendidas"
  affects:
    - "obsidian_vault/adrs/*"
    - "okf/contratos/*"
    - "okf/licoes/*"
    - "a2a_audit.jsonl"
  supersedes: []
  superseded_by: null
---

# ADR-015 — Schema causal de proveniência para ADR, auditoria A2A e OKF

**Status:** Aceito (decisão canônica de governança e rastreabilidade)  
**Data:** 2026-09-23  
**Contexto técnico:** `/root/.haos/obsidian_vault/adrs/` · `/root/.haos/okf/` · `/root/.haos/a2a_audit.jsonl` · `/root/.haos/okf/contratos/proveniencia-prov-o-no-haos.md`  
**ADR relacionadas:** ADR-001 (stack de memória), ADR-005 (caminho do vault), ADR-006 (numeração e imutabilidade de ADR), ADR-009 (namespaces e autoridade)

---

## 1. Contexto

O ecossistema HAOS opera através de múltiplos subsistemas de memória: Obsidian Vault para ADRs, base OKF para contratos e lições aprendidas, e arquivos JSONL estruturados para auditoria de interações inter-agente (`a2a_audit.jsonl`).

Historicamente, o rastreamento causal entre essas camadas vinha ocorrendo de forma assistemática:
1. Menções livres no corpo dos documentos (ex: "`**ADR relacionadas:** ADR-001`");
2. Falta de padrão estruturado legível por máquina para derivação ("de qual sessão ou auditoria essa lição se originou?");
3. Risco de adoção de frameworks ontológicos complexos (ex: dependências pesadas de bibliotecas RDF/OWL ou graph databases com overhead excessivo de CPU/RAM em nó local).

Era imperativo padronizar uma semântica causal formal inspirada no padrão W3C PROV-O (Provenance Ontology), porém adaptada à realidade do HAOS: textual, nativa em YAML (frontmatter) e JSON/JSONL, determinística e processável sem adicionar bibliotecas pesadas.

---

## 2. Decisão

### 2.1 Adoção do Padrão PROV-O Simplificado para HAOS
Fica formalmente adotado o schema causal de proveniência baseado nas relações fundamentais do PROV-O, implementado diretamente sobre estruturas YAML e JSON já existentes no HAOS:
- `prov.wasGeneratedBy`: Atividade/execução ou tarefa que gerou a entidade (ex: `task:<id>`, `session:<sid>`).
- `prov.wasAssociatedWith`: Agente ou operador associado à geração (ex: `agent:<id>`, `user:<id>`).
- `prov.wasDerivedFrom`: Artefato ou registro de entrada primário (ex: `adr:ADR-001`, `audit:<task_id>`).
- `causado_by`: Justificativa causal declarativa / gatilho causal do evento ou decisão.
- `affects`: Lista explícita de componentes, subsistemas ou caminhos impactados.
- `supersedes`: Identificador(es) de artefatos que este documento substitui ou invalida.
- `superseded_by`: Identificador do artefato sucessor (null enquanto vigente).

Esta modelagem dispensa qualquer biblioteca externa (como `rdflib`), preservando os recursos do nó e a compatibilidade total com os parsers padrão de YAML/JSON do runtime e scripts Python da plataforma.

A especificação técnica detalhada das propriedades e tipagem fica contratada em `/root/.haos/okf/contratos/proveniencia-prov-o-no-haos.md`.

### 2.2 Autorização Expressa para Enriquecimento de Frontmatter dos ADRs 001 a 014
O princípio estabelecido desde o ADR-001 e reafirmado nos ADR-005, ADR-006 e ADR-009 define que **ADRs aceitas são estritamente imutáveis em seu corpo histórico**. Decisões, deliberações e contextos aprovados não podem ser alterados retroativamente.

No entanto, a ausência de metadados causais estruturados nos ADRs legados (001 a 014) fragmenta o grafo relacional e impede consultas causais automatizadas.

Por meio deste ADR-015:
1. **Fica expressamente autorizada** a adição e padronização do bloco de frontmatter YAML contendo os metadados canônicos e a seção `prov:` nos arquivos `ADR-001` até `ADR-014`.
2. **Invariante preservada:** O corpo do texto (Markdown abaixo do frontmatter) permanece estritamente intocado e inalterado. O enriquecimento é puramente de metadados estruturais de proveniência causal.

---

## 3. Consequências

- **Rastreabilidade Bidirecional:** Qualquer lição OKF, decisão de arquitetura ou evento A2A pode ser traçado deterministicamente até o agente responsável e o evento causal que o desencadeou.
- **Leveza Operacional:** Processamento com complexidade O(1) por documento via parsers nativos de texto/YAML/JSON, sem degradação de I/O ou consumo de RAM.
- **Preservação Histórica:** O patrimônio deliberativo dos ADRs 001–014 ganha indexabilidade causal sem violar a regra de ouro de imutabilidade histórica do corpo textual.
