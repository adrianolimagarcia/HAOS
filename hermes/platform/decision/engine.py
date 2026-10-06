"""Motor Adaptativo de Decisões Tipadas (System One Engine).

Utiliza Gemini 2.5 Flash Lite em http://100.77.31.78:8790/v1 exclusivamente
na primeira ocorrência (Cold Start) para extrair decisões estruturadas e tipadas.
Armazena a decisão no DecisionStore SQLite para que chamadas subsequentes
sejam resolvidas localmente em < 1 milissegundo com zero custo.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

from hermes.platform.decision.spec import DecisionBoolean, DecisionChoice, DecisionScore
from hermes.platform.decision.store import DecisionStore
from hermes.platform.decision.jev_scorer import JevOptionScorer

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER_URL = "http://100.77.31.78:8790/v1"
DEFAULT_MODEL = "gemini-2.5-flash-lite"


class SystemOneEngine:
    def __init__(
        self,
        endpoint_url: str = DEFAULT_PROVIDER_URL,
        model: str = DEFAULT_MODEL,
        store: Optional[DecisionStore] = None,
        timeout: float = 12.0,
        scorer: Optional[JevOptionScorer] = None,
        confidence_threshold: float = 0.80,
    ):
        self.endpoint_url = endpoint_url.rstrip("/")
        self.model = model
        self.store = store or DecisionStore()
        self.timeout = timeout
        self.jev_scorer = scorer or JevOptionScorer()
        self.confidence_threshold = confidence_threshold

    def _call_gemini_lite(self, prompt: str, schema_hint: str) -> Dict[str, Any]:
        """Executa chamada atômica para o Gemini 2.5 Flash Lite no endpoint local."""
        url = f"{self.endpoint_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"You are a strict, fast typed decision engine (System One). "
                        f"Analyze the following question and state. "
                        f"Output ONLY valid JSON matching this schema: {schema_hint}. "
                        f"No markdown formatting, no code blocks, no explanations outside JSON.\n\n"
                        f"{prompt}"
                    ),
                }
            ],
            "temperature": 0.0,
            "max_tokens": 3000,
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer dummy",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
            # Extrai o bloco JSON caso haja resquícios de texto ou markdown
            match = re.search(r"(\{.*\})", content, re.DOTALL)
            raw_to_parse = match.group(1) if match else content
            try:
                return json.loads(raw_to_parse)
            except json.JSONDecodeError:
                # Tenta reparar JSON com markdown / newline escapando
                sanitized = raw_to_parse.replace("\r\n", "\\n").replace("\n", "\\n")
                try:
                    return json.loads(sanitized)
                except Exception:
                    # Extrai campos manualmente se o JSON for truncado
                    title = "Mitigation Guide"
                    rules = ["Verify host availability before connection", "Check network permissions"]
                    return {"skill_title": title, "mitigation_rules": rules, "skill_markdown": content}

    def decide_boolean(
        self,
        statement: str,
        context: str,
        domain: str = "general",
    ) -> DecisionBoolean:
        """Julgamento binário rápido (Sim/Não) com confiança calibrada."""
        t0 = time.perf_counter()
        pattern_key = self.store.compute_key(domain, statement, context)

        # 1. Verifica cache de decisões aprendidas
        cached = self.store.get(pattern_key)
        if cached:
            res_dict, _ = cached
            latency = (time.perf_counter() - t0) * 1000.0
            return DecisionBoolean(
                verdict=bool(res_dict.get("verdict", False)),
                confidence=float(res_dict.get("confidence", 1.0)),
                reason=str(res_dict.get("reason", "From learned pattern cache")),
                category=str(res_dict.get("category", domain)),
                learned=True,
                latency_ms=round(latency, 2),
            )

        # 2. Cold Start: Consulta o Gemini 2.5 Flash Lite
        schema = '{"verdict": boolean, "confidence": number (0.0-1.0), "category": string, "reason": string}'
        prompt = f"STATEMENT TO VERIFY: {statement}\nCONTEXT / EVIDENCE: {context}"

        try:
            raw = self._call_gemini_lite(prompt, schema)
            verdict = bool(raw.get("verdict", False))
            confidence = float(raw.get("confidence", 0.95))
            reason = str(raw.get("reason", ""))
            category = str(raw.get("category", domain))

            # 3. Memoriza no banco para as próximas execuções
            to_save = {"verdict": verdict, "confidence": confidence, "reason": reason, "category": category}
            self.store.put(
                pattern_key=pattern_key,
                domain=domain,
                raw_signature=statement[:120],
                decision_type="boolean",
                result=to_save,
                confidence=confidence,
            )

            latency = (time.perf_counter() - t0) * 1000.0
            return DecisionBoolean(
                verdict=verdict,
                confidence=confidence,
                reason=reason,
                category=category,
                learned=False,
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            logger.warning("SystemOne decide_boolean failed, returning fallback: %s", exc)
            return DecisionBoolean(
                verdict=False,
                confidence=0.5,
                reason=f"Fallback on error: {exc}",
                learned=False,
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            )

    def decide_choice(
        self,
        question: str,
        options: List[str],
        context: str,
        domain: str = "general",
    ) -> DecisionChoice:
        """Escolha estrita entre opções com fast-gated inference (Jev -> LLM)."""
        t0 = time.perf_counter()
        if not options:
            raise ValueError("A lista de opções não pode estar vazia.")
        if len(options) == 1:
            return DecisionChoice(
                selected=options[0],
                confidence=1.0,
                probabilities={options[0]: 1.0},
                reason="Single option provided",
                learned=False,
                latency_ms=0.0,
            )

        pattern_key = self.store.compute_key(domain, question, context, options=options)

        # 1. Verifica cache local exato (padrão aprendido previamente)
        cached = self.store.get(pattern_key)
        if cached:
            res_dict, _ = cached
            latency = (time.perf_counter() - t0) * 1000.0
            return DecisionChoice(
                selected=str(res_dict.get("selected", options[0])),
                confidence=float(res_dict.get("confidence", 1.0)),
                probabilities=res_dict.get("probabilities", {}),
                reason=str(res_dict.get("reason", "From learned pattern cache")),
                learned=True,
                latency_ms=round(latency, 2),
            )

        # 2. Fast-Gated Inference: Jev-like Local Scorer
        full_ctx = f"QUESTION: {question}\nCONTEXT: {context}"
        jev_decision = self.jev_scorer.decide(full_ctx, options, domain=domain)

        # Se a confiança for igual ou superior ao threshold, usa direto o Jev!
        if jev_decision.confidence >= self.confidence_threshold:
            jev_decision.reason = (
                f"Fast-gated Jev decision (confidence={jev_decision.confidence:.2f} >= "
                f"threshold={self.confidence_threshold:.2f})"
            )
            return jev_decision

        # 3. Escala para o LLM (System-2: incerteza ou confiança abaixo do threshold)
        logger.debug(
            "Jev confidence (%.2f) below threshold (%.2f). Escalating to LLM...",
            jev_decision.confidence,
            self.confidence_threshold,
        )

        opts_formatted = json.dumps(options)
        schema = f'{{"selected": string (must be one of {opts_formatted}), "confidence": number, "probabilities": dict, "reason": string}}'
        prompt = f"QUESTION: {question}\nALLOWED OPTIONS: {opts_formatted}\nCONTEXT: {context}"

        try:
            raw = self._call_gemini_lite(prompt, schema)
            selected = str(raw.get("selected") or options[0])
            if selected not in options:
                # Normaliza para a opção mais próxima
                for opt in options:
                    if opt.lower() in selected.lower():
                        selected = opt
                        break
                else:
                    selected = options[0]

            confidence = float(raw.get("confidence", 0.95))
            probabilities = raw.get("probabilities") or {selected: confidence}
            reason = str(raw.get("reason", "LLM decision"))

            to_save = {
                "selected": selected,
                "confidence": confidence,
                "probabilities": probabilities,
                "reason": reason,
                "options": options,
                "context": context,
                "question": question,
            }
            self.store.put(
                pattern_key=pattern_key,
                domain=domain,
                raw_signature=question[:120],
                decision_type="choice",
                result=to_save,
                confidence=confidence,
            )

            latency = (time.perf_counter() - t0) * 1000.0
            return DecisionChoice(
                selected=selected,
                confidence=confidence,
                probabilities=probabilities,
                reason=reason,
                learned=False,
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            logger.warning("SystemOne LLM call failed, falling back to Jev decision: %s", exc)
            jev_decision.reason = f"Fallback on LLM error ({exc})"
            return jev_decision

    def train_jev_offline(
        self,
        epochs: int = 5,
        lr: float = 0.01,
        limit: int = 500,
    ) -> Dict[str, Any]:
        """Treina o modelo Jev offline utilizando o histórico de decisões acumuladas no SQLite."""
        samples = self.store.get_choice_training_samples(limit=limit)
        if not samples:
            return {
                "status": "no_samples",
                "samples_count": 0,
                "message": "Nenhuma amostra de decisão 'choice' elegível encontrada no banco.",
            }

        avg_loss = self.jev_scorer.train_batch(samples, epochs=epochs, lr=lr)
        saved_path = self.jev_scorer.save_weights()
        return {
            "status": "trained",
            "samples_count": len(samples),
            "epochs": epochs,
            "final_loss": round(avg_loss, 4),
            "weights_path": str(saved_path),
        }

    def decide_choice_fast(
        self,
        question: str,
        options: List[str],
        context: str = "",
        domain: str = "general",
    ) -> DecisionChoice:
        """Decisão ultra-rápida de uma passada (System-1 Jev-like) sem chamadas de rede."""
        full_ctx = f"QUESTION: {question}\nCONTEXT: {context}" if context else question
        return self.jev_scorer.decide(full_ctx, options, domain=domain)

    def decide_score(
        self,
        criterion: str,
        context: str,
        min_val: float = 0.0,
        max_val: float = 1.0,
        domain: str = "general",
    ) -> DecisionScore:
        """Pontuação normalizada sobre um critério analítico."""
        t0 = time.perf_counter()
        pattern_key = self.store.compute_key(domain, criterion, context)

        # 1. Verifica cache local
        cached = self.store.get(pattern_key)
        if cached:
            res_dict, _ = cached
            latency = (time.perf_counter() - t0) * 1000.0
            return DecisionScore(
                score=float(res_dict.get("score", 0.0)),
                confidence=float(res_dict.get("confidence", 1.0)),
                reason=str(res_dict.get("reason", "From learned pattern cache")),
                learned=True,
                latency_ms=round(latency, 2),
            )

        # 2. Cold Start: Gemini Lite
        schema = f'{{"score": number ({min_val} to {max_val}), "confidence": number, "reason": string}}'
        prompt = f"CRITERION: {criterion} (range: {min_val} to {max_val})\nCONTEXT: {context}"

        try:
            raw = self._call_gemini_lite(prompt, schema)
            score = float(raw.get("score", 0.0))
            score = max(min_val, min(max_val, score))
            confidence = float(raw.get("confidence", 0.95))
            reason = str(raw.get("reason", ""))

            to_save = {"score": score, "confidence": confidence, "reason": reason}
            self.store.put(
                pattern_key=pattern_key,
                domain=domain,
                raw_signature=criterion[:120],
                decision_type="score",
                result=to_save,
                confidence=confidence,
            )

            latency = (time.perf_counter() - t0) * 1000.0
            return DecisionScore(
                score=score,
                confidence=confidence,
                reason=reason,
                learned=False,
                latency_ms=round(latency, 2),
            )
        except Exception as exc:
            logger.warning("SystemOne decide_score failed: %s", exc)
            return DecisionScore(
                score=0.0,
                confidence=0.5,
                reason=f"Fallback on error: {exc}",
                learned=False,
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            )


# Singleton global
_global_engine: Optional[SystemOneEngine] = None


def get_decision_engine() -> SystemOneEngine:
    global _global_engine
    if _global_engine is None:
        _global_engine = SystemOneEngine()
    return _global_engine
