---
id: ADR-028
titulo: "Contrato de Ciclo de Vida Temporal e observed_at na Memória Canônica (Absorção GBrain Etapa 1)"
status: "Aceito e Implementado"
data: 2026-10-06
decidido_por: "human:adriano"
autor: "adriano / HAOS Subagent"
contexto: "Etapa 1 da absorção de memória do GBrain: formalização do ciclo de vida temporal, diferenciação de observed_at e valid_from, e determinismo com relógio injetável"
causado_by:
  - "adr:ADR-001"
  - "adr:ADR-014"
  - "adr:ADR-015"
  - "adr:ADR-022"
  - "plano:PLANO-ABSORCAO-GBRAIN-MEMORIA.md"
evidence:
  - "Auditoria AUDITORIA-MEMORIA.md (GAP-02 confirmada): falta de separação entre observed_at e valid_from"
  - "Auditoria AUDITORIA-MEMORIA.md (GAP-01): schemas possuíam is_valid_at sem chamadores no recall e com cálculo inconsistente"
  - "Migração aditiva validada sobre cópia de base real de produção (/root/.haos/memory/fabric.db)"
affects:
  - "hermes/platform/context/memory/canonical_store.py"
  - "hermes/platform/context/memory/schemas.py"
  - "tests/platform/context/memory/test_temporal_lifecycle.py"
  - "okf/contratos/ciclo-de-vida-memoria-temporal.md"
supersedes: []
superseded_by: null
prov:
  wasGeneratedBy: "task:gbrain-etapa1-contrato-temporal"
  wasAssociatedWith: "agent:haos-cachyos-x8664"
  wasDerivedFrom: "adr:ADR-022"
  causado_by: "necessidade de distinguir timestamp do fato no mundo real vs registro no sistema e formalizar expiração puramente calculada"
  affects:
    - "hermes/platform/context/memory/canonical_store.py"
    - "hermes/platform/context/memory/schemas.py"
    - "tests/platform/context/memory/test_temporal_lifecycle.py"
    - "okf/contratos/ciclo-de-vida-memoria-temporal.md"
  supersedes: []
  superseded_by: null
---

# ADR-028: Contrato de Ciclo de Vida Temporal e observed_at na Memória Canônica

## 1. Contexto e Motivação

Durante a Auditoria Técnica do pipeline de memória do HAOS (Etapa 0 da absorção seletiva do GBrain, registrada em `AUDITORIA-MEMORIA.md`), confirmou-se o **GAP-02**: a estrutura `MemoryRecord` e a tabela canônica `memory_records` continham apenas `valid_from` e `created_at`. Não existia discriminação entre:
1. **Quando o fato ocorreu no mundo real** (`observed_at`);
2. **Quando o fato foi registrado / tornou-se canônico no sistema** (`valid_from`);
3. **Quando o registro foi persistido fisicamente** (`created_at`).

Essa ausência causava ambiguidade no cálculo de frescor da evidência, distorcendo o ranking do RRF e o decaimento temporal de memórias históricas ou retroativas.

Adicionalmente, havia a necessidade de consolidar o contrato de ciclo de vida temporal (estados `proposed`, `active`, `superseded`, `retracted`), garantindo a invariante de **expiração calculada** (a expiração é derivada da comparação temporal no recall, jamais reescrita no banco) e **relógio injetável determinístico** (`now: float | None`) para eliminar flakiness em testes.

## 2. Decisão Arquitetural

1. **Campo `observed_at` Aditivo em `MemoryRecord` e SQLite:**
   - Adicionado o campo `observed_at: Optional[float] = None` em `MemoryRecord`.
   - Invariante: se `observed_at is None`, assume por padrão o valor de `valid_from` no `__post_init__`.
   - Adicionada migração aditiva idempotente em `CanonicalMemoryStore._migrate()`:
     ```sql
     ALTER TABLE memory_records ADD COLUMN observed_at REAL;
     UPDATE memory_records SET observed_at = valid_from WHERE observed_at IS NULL;
     ```
   - Inserções novas gravam explicitamente `observed_at`, garantindo integridade estrita.

2. **Política de Ciclo de Vida e Expiração Calculada:**
   - Estados canônicos formais: `proposed` (em staging), `active` (vigente), `superseded` (substituído por revisão), `retracted` (revogado explicitamente).
   - Intervalo de vigência semiaberto: $[valid\_from, valid\_until)$ (início inclusivo, fim exclusivo).
   - **Expiração é puramente calculada**: se $valid\_until \le now$, o registro é inativo para recall dinâmico. O banco de dados nunca é mutado para registrar expiração.
   - **Registros legados sem data**: tratados como frescor desconhecido (`freshness unknown`). Nenhuma vigência é inventada artificialmente.
   - **Esquecimento sem deleção física (`forget != apagar`)**: exclusão física (`DELETE`) é proibida na memória canônica; o histórico é preservado para auditoria.

3. **Relógio Injetável Determinístico:**
   - Métodos temporais em `KnowledgeItem` e `MemoryRecord` (`is_valid_at`, `status_at`, `is_active_at`) recebem o parâmetro `now: float | None = None` (com fallback determinístico para `time.time()`).

## 3. Evidências e Testes

- 18 arquivos e 138 testes executados via `./scripts/run_tests.sh tests/platform/context/memory/`: 100% aprovados (0 falhas).
- Testes invariantes dedicados criados em `tests/platform/context/memory/test_temporal_lifecycle.py`:
  - Limite semiaberto $[valid\_from, valid\_until)$ validado com precisão de milissegundos.
  - Registro legado sem datas sem fabricação de vigência.
  - Relógio injetável determinístico no passado e futuro.
  - Migração aditiva em SQLite com dados reais preservados e preenchimento de `observed_at`.
- Teste real executado contra cópias temporárias isoladas de `/root/.haos/memory/fabric.db` (53 registros preservados com integridade perfeita, zero campos nulos após migração) e `/root/.haos/state.db`.

## 4. Consequências

- **Positivas:**
  - Frescor de evidências auditável e transparente para a Etapa 3 (Recall fundamentado).
  - Preservação total de compatibilidade regressiva com dados existentes e código legado.
  - Testes temporais deterministicamente reproduzíveis sem manipulação de relógio do sistema operacional.
- **Negativas / Limitações:**
  - Exige que leitores em Rust (`haos-edge`) e Python integrem a filtragem de `valid_until` nas próximas etapas (Etapa 2 do plano).
