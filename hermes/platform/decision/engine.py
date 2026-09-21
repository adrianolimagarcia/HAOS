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
    ):
        self.endpoint_url = endpoint_url.rstrip("/")
        self.model = model
        self.store = store or DecisionStore()
        self.timeout = timeout

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
            "max_tokens": 1200,
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
            if match:
                return json.loads(match.group(1))
            return json.loads(content)

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
        """Escolha estrita entre opções permitidas com probabilidades estimadas."""
        t0 = time.perf_counter()
        pattern_key = self.store.compute_key(domain, question, context, options=options)

        # 1. Verifica cache local
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

        # 2. Cold Start: Consulta Gemini Lite
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
            reason = str(raw.get("reason", ""))

            to_save = {
                "selected": selected,
                "confidence": confidence,
                "probabilities": probabilities,
                "reason": reason,
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
            logger.warning("SystemOne decide_choice failed: %s", exc)
            return DecisionChoice(
                selected=options[0] if options else "unknown",
                confidence=0.5,
                reason=f"Fallback on error: {exc}",
                learned=False,
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 2),
            )

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
