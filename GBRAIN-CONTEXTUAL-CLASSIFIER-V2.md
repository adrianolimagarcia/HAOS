# GBRAIN CONTEXTUAL MEMORY CLASSIFICATION V2 — PLANO E EXECUÇÃO

Data: 2026-10-09  
Branch de Integração: `integration/gbrain-contextual-classifier-v2-20261009`  
Worktree de Integração: `/tmp/wt-gbrain-classifier-v2`  
Status: EM EXECUÇÃO (Fase 1: Delegação e Planejamento)

---

## 1. Contexto e Objetivos

O sistema atual de consolidação de memórias (GBrain / Dream / Curator / Staging / Fabric) utiliza uma heurística simples (`contains_hypothesis`) baseada em regex de palavras-chave. Isso gera dois problemas críticos:
1. **Falsos Positivos em Citações e Fatos Objetivos:** Frases como "Fulano disse: talvez o servidor caia" ou fatos afirmando que "A hipótese nula foi rejeitada no teste" acabavam tratadas como hipótese do próprio turno.
2. **Falsos Negativos em Modalizações Implícitas:** Afirmações como "deve ser problema de rede", "parece que a porta caiu" ou suposições disfarçadas de conclusão sem evidência passavam como fatos e eram promovidas indevidamente.
3. **Falta de Contrato Tipado e Auditável:** Não havia classificação explícita de afirmações (`FACT_VERIFIED`, `FACT_UNVERIFIED`, `HYPOTHESIS`, `REFUTED`), rastreabilidade de evidências (`evidence_refs`), id de afirmação refutada (`refuted_assertion_id`), nem rationale codes.

**Meta:** Impedir que hipóteses, incertezas e diagnósticos provisórios sejam promovidos como fatos, mantendo histórico/proveniência, retrocompatibilidade estrita e auditabilidade.

---

## 2. Arquitetura e Contrato Tipado

### 2.1 Enums e Tipos Canônicos (`AssertionClassification`)
```python
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import time
import uuid

class AssertionType(str, Enum):
    FACT_VERIFIED = "FACT_VERIFIED"
    FACT_UNVERIFIED = "FACT_UNVERIFIED"
    HYPOTHESIS = "HYPOTHESIS"
    REFUTED = "REFUTED"

@dataclass
class AssertionClassification:
    assertion_type: AssertionType
    confidence: float                          # Confiança linguística do classificador (0.0 a 1.0)
    rationale_codes: List[str]                 # Ex: ["EXPLICIT_HYPOTHESIS_MARKER", "CITATION_ATTRIBUTION", "VERIFIED_OBSERVATION"]
    evidence_refs: List[str] = field(default_factory=list)  # URIs / referências a logs, testes, saídas observadas
    source_provenance: str = ""                # Proveniência da asserção (ex: session://..., tool://...)
    assertion_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    refuted_assertion_id: Optional[str] = None # ID de asserção anterior refutada
    observed_at: float = field(default_factory=time.time)
    valid_from: Optional[float] = None
    valid_until: Optional[float] = None
    raw_text: str = ""
    is_cited: bool = False                     # Indica se o conteúdo é citado/atribuído a terceiro
```

### 2.2 Regras de Transição e Gating
- `FACT_VERIFIED`: Exige **evidência verificável** (`evidence_refs` não vazia e verificada) OU confirmação explícita de observação/teste direto com retorno (`OBSERVED`, `exit_code == 0`, log direto). Alta confiança linguística **nunca** é suficiente por si só para `FACT_VERIFIED`.
- `FACT_UNVERIFIED`: Afirmações sem marcadores de dúvida/hipótese, mas sem evidências concretas anexadas. **Não elegível** para auto-promoção canônica como verdade comprovada.
- `HYPOTHESIS`: Presença de modalizadores explícitos ou implícitos de incerteza, suposição, conjectura, ou deduções não verificadas. Bloqueio total no gate de promoção.
- `REFUTED`: Afirmação que contradiz ou refuta explicitamente uma asserção anterior (`refuted_assertion_id`), registrando vigência/tombstone sem deletar histórico.

---

## 3. Diagrama de Fluxo e Responsabilidades

```
[Entrada de Texto / Turno / Sessão]
                 │
                 ▼
     ┌────────────────────────┐
     │ Contextual Classifier  │ (Heurística PT-BR/EN + Detecção Citações/Modalizadores)
     └───────────┬────────────┘
                 │
                 ├──────────────────────────────────────────────────┐
                 ▼                                                  ▼
     [AssertionClassification]                             [contains_hypothesis()]
                 │                                         (Retrocompatibilidade)
                 ▼
     ┌────────────────────────┐
     │   MemoryCandidate      │ (Armazena assertion_classification de forma aditiva)
     └───────────┬────────────┘
                 │
                 ▼
     ┌────────────────────────┐
     │     Router / Gate      │ ───► HYPOTHESIS / FACT_UNVERIFIED ──► Staging (status='pending')
     └───────────┬────────────┘
                 │
                 ├──► FACT_VERIFIED (com evidence_refs) ──► Elegível para Promoção
                 │
                 └──► REFUTED (com refuted_assertion_id) ──► Histórico Preservado / Supersedes
```

### 3.1 Owner / Writer / Reader
- **Frente A (Classifier):** `hermes/platform/context/memory/classifier.py` + `candidate.py` (compatibilidade).
- **Frente B (Evidence / Provenance / Candidate):** `candidate.py`, `staging.py`, `federated_fabric.py` (aditivo).
- **Frente C (Dream / Curator / Promotion Gate):** `hermes/platform/memory/dream.py`, `reconciler.py`, `router.py`.
- **Frente D (Eval / Independent Review):** `evals/gbrain_memory/test_contextual_classification_v2.py` + corpus congelado sintético (>=80 casos PT/EN).

---

## 4. Invariantes
1. **Prompt Caching & Sem Quebra de Contrato:** Nenhum leitor existente que chama `contains_hypothesis(text)` tem seu comportamento quebrado. A assinatura é mantida e a precisão aprimorada.
2. **Fail-Closed:** Na dúvida ou ambiguidade, classificar como `FACT_UNVERIFIED` ou `HYPOTHESIS`, **jamais** `FACT_VERIFIED`.
3. **Preservação Histórica:** Memórias refutadas (`REFUTED`) não são apagadas via `DELETE`; recebem marcação e vínculo via `refuted_assertion_id`/`supersedes`.
4. **Isolamento de Credenciais e Segurança:** Manter filtros estritos contra segredos, chaves de API e instruções adversárias (prompt injection em inputs não confiáveis).
5. **Sem Escrita Direta em Banco Real por Subagentes:** Todos os testes usam fixtures isoladas (`tmp_path`).

---

## 5. Estratégia de Rollback
Se a integração apresentar qualquer regressão no baseline GBrain ou falha estrutural:
1. Reverter o branch `integration/gbrain-contextual-classifier-v2-20261009` para o commit base `main`.
2. As implementações são puramente aditivas e autocontidas; desativar a flag contextual reverte o gate para a heurística original de `candidate.py`.
