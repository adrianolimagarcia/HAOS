"""Ouroboros Evolution Engine — alimentado (K7, Emenda 19).

Shadow mode REAL: proposals são calculadas de dados (observability metrics +
eval baselines), nunca texto fixo. O analyzer não opina sobre o que não mede:

* ``compare_baselines`` — delta entre dois labels de uma suite no
  ``BaselineStore`` (ex.: model-a vs model-b, hermes-upstream vs haos-fork).
  Toda rationale carrega os números medidos (pass_rate/avg_score de cada lado
  e o delta); a proposta muda quando os dados mudam.
* ``analyze_metrics`` — falhas/erros, custo e latência do
  ``MetricsCollector.summary()``, com budgets opcionais do chamador. Sem
  contador relevante ou com métrica dentro do orçamento, não há proposta.
* ``analyze_execution_history`` — facada que junta baselines + metrics +
  eventos (``event_store``/``event_logs``) numa única lista, sempre em
  ``mode="PROPOSAL_ONLY"`` (shadow: nada é aplicado automaticamente).

Forma da proposal (compat com a versão stub): dict com ``target``,
``current_profile``, ``proposed_profile``, ``rationale`` e ``mode``.
"""

from typing import Any, Dict, List, Optional

# Números em comum nas métricas de eval que o delta compara.
_EVAL_NUMERIC_KEYS = ("pass_rate", "avg_score", "precision", "recall", "f1")


def _numeric(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class OuroborosAnalyzer:
    """Evolução em shadow mode: propostas derivadas de dados, sem texto fixo."""

    # ------------------------------------------------------------------ #
    # baselines
    # ------------------------------------------------------------------ #
    def compare_baselines(
        self,
        baseline_store,
        suite_id: str,
        candidate_label: str,
        reference_label: str,
        *,
        min_delta: float = 1e-9,
    ) -> List[Dict[str, Any]]:
        """Compara o run mais recente de *candidate* contra o de *reference* na
        suite e propõe promoção/rollback com os números medidos."""
        def last(label: str) -> Optional[Dict[str, Any]]:
            for row in reversed(baseline_store.history(suite_id, label)):
                return row
            return None

        cand = last(candidate_label)
        ref = last(reference_label)
        if cand is None or ref is None:
            return []  # sem dados não há opinião
        cand_m, ref_m = cand["metrics"], ref["metrics"]

        deltas = {}
        for key in _EVAL_NUMERIC_KEYS:
            a, b = _numeric(cand_m.get(key)), _numeric(ref_m.get(key))
            if a is not None and b is not None:
                deltas[key] = round(a - b, 4)

        if not deltas:
            return []

        improved = {k: v for k, v in deltas.items() if v > min_delta}
        regressed = {k: v for k, v in deltas.items() if v < -min_delta}

        proposals: List[Dict[str, Any]] = []
        if improved or regressed:
            side = "melhor" if (improved and not regressed) else "pior"
            direction = "promover" if side == "melhor" else "segurar/rollback"
            bits = "; ".join(
                f"{k} {ref_m.get(k)} -> {cand_m.get(k)} (Δ{v:+.4f})" for k, v in deltas.items()
            )
            proposals.append({
                "target": f"suite:{suite_id}",
                "current_profile": reference_label,
                "proposed_profile": candidate_label if side == "melhor" else reference_label,
                "rationale": (
                    f"Eval '{suite_id}': {candidate_label} ficou {side} que "
                    f"{reference_label} em {bits}. Shadow: {direction} {candidate_label}."
                ),
                "mode": "PROPOSAL_ONLY",
                "evidence": {
                    "suite_id": suite_id,
                    "candidate": {"label": candidate_label, "metrics": cand_m},
                    "reference": {"label": reference_label, "metrics": ref_m},
                    "deltas": deltas,
                    "verdict": "improved" if side == "melhor" else "regressed",
                },
            })
        return proposals

    # ------------------------------------------------------------------ #
    # metrics
    # ------------------------------------------------------------------ #
    def analyze_metrics(
        self,
        metrics: Dict[str, Any],
        *,
        max_cost_usd: Optional[float] = None,
        failure_rate_threshold: float = 0.15,
        latency_warn_s: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Propostas de métricas observadas. Contadores com nomes contendo
        'fail'/'error' são falhas; 'complete'/'success'/'ok' são sucessos.
        Custo/latência usam os budgets do chamador quando informados."""
        counters = metrics.get("counters") or {}
        failed = sum(v for k, v in counters.items() if ("fail" in k or "error" in k))
        ok = sum(v for k, v in counters.items() if ("success" in k or "complete" in k or k.endswith(".ok")))

        proposals: List[Dict[str, Any]] = []
        total = failed + ok
        if total > 0 and failed / total > failure_rate_threshold:
            rate = round(failed / total, 4)
            proposals.append({
                "target": "observability:runs",
                "current_profile": "unknown",
                "proposed_profile": "triage/rollback",
                "rationale": (
                    f"Shadow mediu {failed} falha(s) em {total} runs "
                    f"(taxa {rate:.1%} > limite {failure_rate_threshold:.1%}). "
                    "Proposta: triagem/rollback antes de promover qualquer perfil."
                ),
                "mode": "PROPOSAL_ONLY",
                "evidence": {"failed": failed, "ok": ok, "failure_rate": rate},
            })

        cost = _numeric(metrics.get("total_cost_usd"))
        if cost is not None and max_cost_usd is not None and cost > max_cost_usd:
            proposals.append({
                "target": "observability:cost",
                "current_profile": "unknown",
                "proposed_profile": "profile-menor-custo",
                "rationale": (
                    f"Shadow mediu custo {cost:.4f} USD acima do orçamento "
                    f"{max_cost_usd:.4f} USD. Proposta: avaliar perfil de menor custo."
                ),
                "mode": "PROPOSAL_ONLY",
                "evidence": {"total_cost_usd": cost, "max_cost_usd": max_cost_usd},
            })

        latencies = metrics.get("latencies") or {}
        if latency_warn_s is not None:
            for op, dur in sorted(latencies.items()):
                if _numeric(dur) is not None and dur > latency_warn_s:
                    proposals.append({
                        "target": f"observability:latency:{op}",
                        "current_profile": "unknown",
                        "proposed_profile": "otimizar/degradação",
                        "rationale": (
                            f"Shadow mediu latência {dur:.2f}s em '{op}' acima do "
                            f"limite {latency_warn_s:.2f}s."
                        ),
                        "mode": "PROPOSAL_ONLY",
                        "evidence": {"operation": op, "latency_s": dur, "warn_s": latency_warn_s},
                    })
        return proposals

    # ------------------------------------------------------------------ #
    # facade
    # ------------------------------------------------------------------ #
    def analyze_execution_history(
        self,
        event_logs: Optional[List[Dict[str, Any]]] = None,
        *,
        metrics: Optional[Dict[str, Any]] = None,
        event_store=None,
        baseline_store=None,
        suite_id: Optional[str] = None,
        candidate_label: Optional[str] = None,
        reference_label: Optional[str] = None,
        max_cost_usd: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Agrega as fontes reais numa única lista de proposals (shadow).

        Compat: ``event_logs`` continua aceito (lista de dicts de eventos);
        quem prefere a fonte viva passa ``event_store``.
        """
        proposals: List[Dict[str, Any]] = []

        if baseline_store is not None and suite_id and candidate_label and reference_label:
            proposals.extend(self.compare_baselines(
                baseline_store, suite_id, candidate_label, reference_label,
            ))

        if metrics is not None:
            proposals.extend(self.analyze_metrics(metrics, max_cost_usd=max_cost_usd))

        if event_store is not None:
            event_logs = [e.to_dict() for e in event_store.get_all()]
        if event_logs:
            proposals.extend(self._proposals_from_events(event_logs))
        return proposals

    @staticmethod
    def _proposals_from_events(event_logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sinais diretos do event stream (shadow): falhas estruturadas de tarefas,
        categorias do FailureClassifier, ou eventos de eval com score abaixo de 1.0."""
        proposals: List[Dict[str, Any]] = []

        # 1. Análise detalhada por failure_category estruturada (11 categorias)
        task_failures = [
            e for e in event_logs
            if str(e.get("name", "")) == "task.run.failure"
        ]
        
        # Agrupamento por modelo e categoria de falha
        by_model_category: Dict[str, Dict[str, int]] = {}
        for tf in task_failures:
            payload = tf.get("payload") or {}
            model = str(payload.get("model") or "unknown_model")
            category = str(payload.get("failure_category") or payload.get("outcome") or "generic_error")
            by_model_category.setdefault(model, {})
            by_model_category[model][category] = by_model_category[model].get(category, 0) + 1

        for model, cat_counts in by_model_category.items():
            total_model_failures = sum(cat_counts.values())
            # Se houver falhas de aceitação ou rejeição de review significativas
            critical_flaws = cat_counts.get("acceptance_failure", 0) + cat_counts.get("review_rejection", 0)
            if critical_flaws > 0:
                proposals.append({
                    "target": f"model_profile:{model}",
                    "current_profile": model,
                    "proposed_profile": f"{model}:high_fidelity",
                    "rationale": (
                        f"Modelo '{model}' acumulou {critical_flaws} falha(s) de aceitação/revisão "
                        f"({cat_counts}). Proposta: elevar postura de verificação ou alternar modelo."
                    ),
                    "mode": "PROPOSAL_ONLY",
                    "evidence": {
                        "model": model,
                        "critical_flaws": critical_flaws,
                        "failure_breakdown": cat_counts,
                    },
                })
            elif total_model_failures >= 2:
                proposals.append({
                    "target": f"model_profile:{model}",
                    "current_profile": model,
                    "proposed_profile": f"{model}:fallback_route",
                    "rationale": (
                        f"Modelo '{model}' registrou {total_model_failures} falhas transientes/operacionais "
                        f"({cat_counts}). Proposta: revisar rota de failover de providers."
                    ),
                    "mode": "PROPOSAL_ONLY",
                    "evidence": {
                        "model": model,
                        "total_failures": total_model_failures,
                        "failure_breakdown": cat_counts,
                    },
                })

        # 2. Falhas genéricas no stream
        generic_failures = [
            e for e in event_logs
            if ("fail" in str(e.get("name", "")) or "error" in str(e.get("name", "")))
            and str(e.get("name", "")) != "task.run.failure"
        ]
        if generic_failures:
            names = sorted({str(e.get("name")) for e in generic_failures})
            proposals.append({
                "target": "observability:events",
                "current_profile": "unknown",
                "proposed_profile": "investigar",
                "rationale": (
                    f"Shadow viu {len(generic_failures)} evento(s) de falha no stream "
                    f"({', '.join(names)}). Proposta: investigar antes de promover."
                ),
                "mode": "PROPOSAL_ONLY",
                "evidence": {"failures": len(generic_failures), "names": names},
            })

        # 3. Evals com baixo desempenho
        underperforming = [
            e for e in event_logs
            if "eval" in str(e.get("name", ""))
            and _numeric((e.get("payload") or {}).get("score")) not in (None, 1.0)
        ]
        if underperforming:
            scores = [
                round(float((e.get("payload") or {}).get("score")), 4) for e in underperforming
            ]
            proposals.append({
                "target": "observability:evals",
                "current_profile": "unknown",
                "proposed_profile": "revisar-suite",
                "rationale": (
                    f"Shadow viu {len(underperforming)} eval(s) com score < 1.0 "
                    f"(scores {scores}). Proposta: revisar suite/candidato."
                ),
                "mode": "PROPOSAL_ONLY",
                "evidence": {"underperforming_scores": scores},
            })

        # 4. Context Utilization Sub-ótima (Context Engine Optimization)
        context_evals = [
            e for e in event_logs
            if str(e.get("name", "")) == "context.utilization.eval"
            or (e.get("payload") or {}).get("metric") == "context_utilization"
        ]
        for ce in context_evals:
            payload = ce.get("payload") or {}
            utilization = _numeric(payload.get("utilization_ratio") or payload.get("score"))
            if utilization is not None and utilization < 0.35:
                proposals.append({
                    "target": "context:budget",
                    "current_profile": "broad_allocation",
                    "proposed_profile": "aggressive_progressive_disclosure",
                    "rationale": (
                        f"Context utilization ratio foi de {utilization:.2%} (< 35%), "
                        f"indicando desperdício de tokens de contexto não referenciados. "
                        f"Proposta: aumentar threshold de elisão em ArtifactStore e compactar LSP para 'summary'."
                    ),
                    "mode": "PROPOSAL_ONLY",
                    "evidence": {
                        "task_id": payload.get("task_id"),
                        "utilization_ratio": utilization,
                        "allocated_tokens": payload.get("allocated_tokens"),
                    },
                })
        return proposals
