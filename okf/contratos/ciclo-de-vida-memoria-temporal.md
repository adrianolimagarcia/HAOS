---
title: Ciclo de Vida e Vigência de Memória Temporal no HAOS
type: api-contract
tags:
  - contrato
  - memoria
  - ciclo-de-vida
  - temporalidade
  - gbrain
  - tempr
  - okf
owner: adriano
doc_type: api-contract
prov:
  wasGeneratedBy: "task:gbrain-etapa1-contrato-temporal"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "contract:contrato-memoria-haos-nos-cachyos.md"
  causado_by: "formalizacao do ciclo de vida temporal, estados de vigencia e semantica de expiracao calculada no Memory Fabric"
  affects:
    - "hermes/platform/context/memory/*"
    - "okf/contratos/*"
    - "obsidian_vault/adrs/*"
  supersedes: []
  superseded_by: null
---

# Contrato Técnico: Ciclo de Vida e Vigência de Memória Temporal no HAOS

**Status:** Ativo / Vigente  
**Autoridade:** ADR-028 (`ADR-028-contrato-ciclo-de-vida-memoria-temporal.md`), ADR-015, ADR-022  
**Data:** 2026-10-06  
**Dono:** adriano (dono do nó) / Hermes Agent  

---

## 1. Objetivo e Filosofia

Este contrato estabelece as regras formais e invariantes para o ciclo de vida temporal de registros na memória canônica do HAOS (Memory Fabric), abrangendo:
1. Estados formais de ciclo de vida (`proposed`, `active`, `superseded`, `retracted`);
2. Distinção semântica estrita entre tempo de ocorrência (`observed_at`), início de vigência formal (`valid_from`) e inserção física (`created_at`);
3. Regra de ouro da expiração calculada (nenhuma reescrita destrutiva de dados ou status para expirar registros);
4. Tratamento determinístico de registros legados sem datas (frescor desconhecido sem criação artificial de vigência);
5. Princípio de esquecimento sem destruição (`forget != apagar`), mantendo a imutabilidade do journal transacional.

---

## 2. Estados Canônicos de Ciclo de Vida

Todo registro de conhecimento ou memória percorre estados formais mutuamente exclusivos:

| Estado | Significado Semântico | Elegível para Recall Canônico? | Mutabilidade |
|---|---|---|---|
| `proposed` | Candidato a fato gerado por turnos, dream ou agentes em staging. Aguarda consolidação ou aprovação. | **NÃO** | Transitório em staging. |
| `active` | Fato canônico consolidado, vigente e confiável. | **SIM** (se dentro da janela de validade) | Append-only no journal canônico. |
| `superseded` | Fato superado por uma nova revisão ou contradição posterior declarada/detectada. Mantém link causal com o substituto. | **NÃO** | Imutável (preservado para auditoria e histórico). |
| `retracted` | Fato expressamente retirado/revogado por decisão de governança ou refutação empírica (tombstone). | **NÃO** | Imutável no journal. |

---

## 3. Semântica Temporal: `observed_at` vs `valid_from` vs `created_at`

Para evitar a ambiguidade de frescor da evidência identificada na Auditoria de Memória (GAP-02):

- **`observed_at` (Quando o fato era verdade no mundo real):**
  - Timestamp (UNIX epoch seconds) que reflete o momento em que a evidência, medição, evento ou situação realmente ocorreu no mundo físico/externo.
  - Opcional no schema; default = `valid_from` quando não fornecido.
  - Usado para medição de frescor empírico, decaimento epistêmico e fundamentação cronológica.
- **`valid_from` (Quando o fato tornou-se ativo no sistema):**
  - Timestamp (UNIX epoch seconds) a partir do qual o registro passa a ter validade autoritativa no HAOS.
  - Define o início do intervalo de vigência formal.
- **`created_at` (Quando a linha foi inserida no banco de dados):**
  - Timestamp (UNIX epoch seconds) da operação física de `INSERT` no journal SQLite.
  - Propriedade puramente de infraestrutura e auditoria transacional.

---

## 4. Invariantes de Vigência e Expiração

### 4.1 Expiração é CALCULADA, Nunca Reescrita
- A expiração de um registro **NUNCA** reescreve a coluna `status` no banco de dados nem muta o registro gravado.
- A vigência é determinada deterministicamente pela expressão temporal do intervalo semiaberto:
  $$[valid\_from, valid\_until)$$
  Um registro é válido no instante $t$ se e somente se:
  $$valid\_from \le t < valid\_until$$
- Se `valid_until` estiver no passado relativo ao tempo de consulta ($valid\_until \le now$), o registro é tratado como **inativo durante o recall**, sem alterar fisicamente sua linha no journal.
- Proibições: Jobs de manutenção, crons ou rotinas noturnas não devem executar `UPDATE memory_records SET status='expired'`. A inatividade por decurso de prazo é puramente dinâmica.

### 4.2 Registros Legados sem Datas: Frescor Desconhecido
- Registros importados ou legados que não possuem `valid_from` ou `valid_until` delimitados (ou com valores nulos) possuem **frescor desconhecido**.
- É **estritamente proibido inventar vigência arbitrária** (como presumir expiração automática em 30 dias para registros sem prazo contratado).
- Tais registros permanecem válidos e ativos enquanto seu status for `active` e não forem explicitamente superados (`superseded_by IS NULL`). No render de recall, sua proveniência é qualificada honestamente como `freshness unknown`.

### 4.3 Forget != Apagar (Exclusão Física Fora de Escopo)
- O comando de "esquecer" (`forget`), invalidar ou retirar um conhecimento gera um registro de retratação (`status='retracted'`) ou supersession, ou encerra a janela temporal definindo `valid_until = now`.
- A exclusão física (`DELETE FROM memory_records`) está **terminantemente fora de escopo**.
- Toda remoção semântica preserva o histórico criptográfico e causal no journal append-only para auditoria e reproducibilidade de decisões passadas.

---

## 5. Determinismo Temporal e Relógio Injetável

Todas as funções de verificação de vigência, cálculo de idade, decaimento de recência e renderização de status temporal devem aceitar um parâmetro injetável:
```python
now: float | None = None
```
- Em tempo de execução interativo padrão, `now is None` utiliza `time.time()`.
- Em testes unitários, testes de regressão e simulações de replay, `now` deve ser fixado explicitamente, garantindo testes 100% reproduzíveis e imunes a race conditions ou transições de fuso horário.
