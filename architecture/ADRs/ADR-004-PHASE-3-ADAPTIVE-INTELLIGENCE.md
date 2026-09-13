# ADR-004: Phase 3 — Adaptive Intelligence Platform (Ouroboros SOTA)

- **Status:** Accepted (Canonical Phase 3 Architecture)
- **Date:** 2026-09-08
- **Authors:** HAOS Architecture Guild & DeepSeek Harness Systems Engineering
- **Phase:** Phase 3 — Adaptive Intelligence Platform
- **Governed By:** ADR-001 (SOTA Multi-Agent), ADR-002 (Architecture Freeze v0.1), ADR-003 (Team Runtime)

---

## 1. Contexto e Objetivo

O objetivo da **Phase 3 — Adaptive Intelligence Platform** é habilitar o Hermes Agent a **aprender continuamente com sua própria execução sem perder o controle humano ou arquitetural**.

### Regra Central e Inviolável
> **Evolution pode propor e experimentar. Evolution NUNCA altera silenciosamente policies, model bindings ou o core.**
> Qualquer promoção segue estritamente o pipeline de isolamento:
> `Runs` $\to$ `Events + Metrics` $\to$ `Evaluator` $\to$ `Pattern Detection` $\to$ `Improvement Candidate` $\to$ `Sandbox` $\to$ `Benchmark` $\to$ `Compare with Baseline` $\to$ `Adopt / Reject`.

---

## 2. Subsistemas da Phase 3

### 2.1. Dynamic Failure Pattern Detector (`FailurePatternDetector`)
- Analisa os `TaskRun` e `TraceSpan` no `EventStore`.
- Classifica falhas em categorias tipadas:
  - `SYNTAX_ERROR` (AST parse failure)
  - `TEST_REGRESSION` (pytest failure)
  - `TOOL_PERMISSION_DENIED` (Capability sandbox violation)
  - `TIMEOUT_EXHAUSTION` (Token limit ou wall-clock overrun)
  - `ANCHORING_BIAS` (Viés de raciocínio não corrigido)
- Agrupa falhas correlacionadas e gera `PatternDiagnostic`.

### 2.2. Adaptive Model & Provider Recommender (`AdaptiveRoutingOptimizer`)
- Monitora a matriz de performance: `(model_identity, provider, posture, task_type)`.
- Mede:
  - Taxa de sucesso real ($P(\text{success})$)
  - Custo por 1k tokens em USD
  - Latência p50 e p95
  - Cota de TTFT (Time to First Token)
- Emite recomendações de otimização de rota (`RouteRecommendation`), por exemplo:
  - "Para postura `coder` em tarefas `unit-test`, migrar primário para `a6api:deepseek-v4-flash` reduz custo em 68% sem degradar eval score."

### 2.3. Agent/Task Affinity Matrix (`AffinityMatrix`)
- Rastreia o histórico de performance de `PooledSpecialist` por domínio de tarefa.
- Calcula afinidade bayesiana para despacho prioritário no `SpecialistPool`.

### 2.4. A/B Experimentation Framework (`ExperimentFramework`)
- Isola variantes em worktrees Kilo efêmeros:
  - **Baseline (A):** Configuração/Skill/Prompt atual.
  - **Candidate (B):** Variante gerada pelo Ouroboros.
- Executa benchmark simultâneo em suite determinística.
- Critério de adoção:
  $$\text{EvalScore}(B) \ge \text{EvalScore}(A) + \delta \quad \land \quad \text{Cost}(B) \le \text{Cost}(A) \times (1 + \epsilon)$$
- Se $\text{EvalScore}(B) < \text{EvalScore}(A)$, aciona **Rollback Automático Instantâneo** com descarte do worktree.

### 2.5. Autonomic Context Policy Optimizer (`ContextPolicyOptimizer`)
- Analisa os `ContextPackage` enviados aos agentes.
- Identifica tokens desperdiçados ou distração de contexto (information noise).
- Ajusta dinamicamente a profundidade do GraphRAG e o nível de disclosure progressivo de documentação.
