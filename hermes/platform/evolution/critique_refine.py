"""Critique-refine loop for the evolution layer (CAMEL absorption #3).

Concept source: CAMEL ``SelfImprovingCoT`` — generate, evaluate with a
structured verdict, threshold per dimension, refine with the critique
feedback, and keep an iteration history (``TraceIteration``).

Design invariants (fail-closed, deterministic):
- ``CritiqueVerdict`` is the only accepted shape: integer scores 0-3 for
  ``correctness`` / ``clarity`` / ``completeness``, plus ``feedback`` and a
  recomputed ``accepted`` flag. The judge's self-declared ``accepted`` is
  NEVER trusted — acceptance is always recomputed from thresholds.
- Malformed judge output (exception, non-dict, missing/out-of-range scores)
  yields ``accepted=False`` — never approve by default.
- The judge is an injected callable ``(text) -> dict``. In tests: a
  deterministic fake. In production: ``build_llm_critique_judge()`` wraps the
  existing ``agent.auxiliary_client.call_llm`` stack (task
  ``evolution_critique``) — same pattern as
  ``hermes.platform.council.promotion_executor``: lazy import, no new HTTP
  client.
- ``critique_refine_loop`` keeps the best text (highest score sum), stops
  early once a verdict is accepted, and feeds the critique feedback back to
  the generator on the next iteration.
- ``judge=None`` makes the loop a no-op (single generation, empty history) so
  callers can wire it opt-in with zero regression.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("hermes.platform.evolution.critique_refine")

#: Event emitted once per evaluated iteration when the loop runs (the curator
#: wires ``on_iteration`` to publish it on the canonical EventStore).
EVENT_CRITIQUE_ITERATION = "civ.evolution.critique_iteration"

#: Scored dimensions (CAMEL SelfImprovingCoP criteria, adapted to proposals).
SCORE_DIMENSIONS: Tuple[str, ...] = ("correctness", "clarity", "completeness")

SCORE_MIN = 0
SCORE_MAX = 3

#: Per-dimension acceptance thresholds: correctness and completeness are
#: strict (>=2), clarity only needs to be readable (>=1).
DEFAULT_CRITIQUE_THRESHOLDS: Dict[str, int] = {
    "correctness": 2,
    "clarity": 1,
    "completeness": 2,
}

#: Feedback comes from an LLM judge (untrusted output): bound its size before
#: it can flow into rationale text or event payloads.
FEEDBACK_MAX_CHARS = 500

#: Task name resolved through the auxiliary client config (``auxiliary.
#: evolution_critique`` explicit override wins, else default provider).
CRITIQUE_LLM_TASK = "evolution_critique"


@dataclass
class CritiqueVerdict:
    """Structured critique of one candidate text (all scores 0-3)."""

    correctness: int = 0
    clarity: int = 0
    completeness: int = 0
    feedback: str = ""
    accepted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def score_sum(self) -> int:
        return self.correctness + self.clarity + self.completeness


@dataclass
class TraceIteration:
    """One evaluated iteration of the critique-refine loop.

    ``text_hash`` is the sha256 of the candidate text (raw text is NOT kept —
    history is metadata-only so it can ride in rationale/event payloads
    without size or injection concerns). ``refined`` is True when this
    iteration's text was produced in response to prior critique feedback.
    """

    iteration: int
    text_hash: str
    verdict: Dict[str, Any] = field(default_factory=dict)
    refined: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def resolve_thresholds(criteria: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Merge caller criteria onto the default thresholds (defensively).

    Accepts either a plain thresholds dict (keys among SCORE_DIMENSIONS) or
    ``{"thresholds": {...}, ...}``. Invalid/unknown entries are ignored —
    a bad threshold never loosens the gate by accident.
    """
    base = dict(DEFAULT_CRITIQUE_THRESHOLDS)
    if not isinstance(criteria, dict):
        return base
    nested = criteria.get("thresholds")
    cand: Dict[str, Any] = nested if isinstance(nested, dict) else criteria
    for dim in SCORE_DIMENSIONS:
        v = cand.get(dim)
        if isinstance(v, bool):
            continue
        if isinstance(v, int) and SCORE_MIN <= v <= SCORE_MAX:
            base[dim] = v
    return base


def _malformed_verdict(reason: str, raw_feedback: Any = None) -> CritiqueVerdict:
    """Fail-closed verdict: everything 0, accepted=False, reason in feedback."""
    fb = str(raw_feedback or "") if raw_feedback is not None else ""
    text = f"malformed critique ({reason})"
    if fb:
        text = f"{text}; {fb}"
    return CritiqueVerdict(
        correctness=0, clarity=0, completeness=0,
        feedback=text[:FEEDBACK_MAX_CHARS], accepted=False,
    )


def evaluate_candidate(
    candidate_text: str,
    criteria: Optional[Dict[str, Any]] = None,
    judge: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> CritiqueVerdict:
    """Run ``judge`` over ``candidate_text`` and parse defensively into a verdict.

    Fail-closed rules (never approve by default):
    - ``judge is None``            -> rejected ("no judge").
    - judge raises                 -> rejected ("judge error").
    - non-dict output              -> rejected ("not a dict").
    - missing / non-int / bool / out-of-range score -> that dimension 0 and
      the verdict is rejected regardless of the other dimensions.
    - ``accepted`` is ALWAYS recomputed from thresholds; a judge-supplied
      ``accepted`` field is ignored.
    """
    thresholds = resolve_thresholds(criteria)

    if judge is None:
        return CritiqueVerdict(
            correctness=0, clarity=0, completeness=0,
            feedback="no judge provided; fail-closed rejection",
            accepted=False,
        )

    try:
        raw = judge(candidate_text)
    except Exception as exc:  # noqa: BLE001 — fail-closed by contract
        logger.warning("critique judge raised %s: %s", type(exc).__name__, exc)
        return _malformed_verdict(f"judge error: {type(exc).__name__}")

    if not isinstance(raw, dict):
        return _malformed_verdict(f"judge output is {type(raw).__name__}, not dict")

    scores: Dict[str, int] = {}
    problems: List[str] = []
    for dim in SCORE_DIMENSIONS:
        v = raw.get(dim)
        if isinstance(v, bool) or not isinstance(v, int) or not (SCORE_MIN <= v <= SCORE_MAX):
            problems.append(f"{dim}={v!r}")
            scores[dim] = SCORE_MIN
        else:
            scores[dim] = v

    raw_feedback = raw.get("feedback")
    if problems:
        return _malformed_verdict(", ".join(problems), raw_feedback)

    feedback = "" if raw_feedback is None else str(raw_feedback)
    accepted = all(scores[d] >= thresholds[d] for d in SCORE_DIMENSIONS)
    return CritiqueVerdict(
        correctness=scores["correctness"],
        clarity=scores["clarity"],
        completeness=scores["completeness"],
        feedback=feedback[:FEEDBACK_MAX_CHARS],
        accepted=accepted,
    )


def text_hash(text: str) -> str:
    """Stable sha256 hex of a candidate text (full length; callers truncate)."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def critique_refine_loop(
    generator: Callable[[Optional[Dict[str, Any]]], str],
    judge: Optional[Callable[[str], Dict[str, Any]]],
    max_iterations: int = 3,
    thresholds: Optional[Dict[str, Any]] = None,
    on_iteration: Optional[Callable[[TraceIteration, CritiqueVerdict], None]] = None,
) -> Tuple[str, List[TraceIteration]]:
    """Generate -> critique -> refine, keeping the best text.

    - ``generator(feedback)`` is called with ``None`` on the first iteration
      and with ``{"iteration", "feedback", "verdict"}`` on every refinement.
    - Iteration stops early as soon as a verdict is ``accepted``.
    - The returned text is the one with the highest score sum (ties: the
      earliest iteration wins — deterministic).
    - ``judge is None`` is a strict no-op: one generation, empty history, so
      opt-in callers keep byte-identical behavior.
    - ``on_iteration(trace, verdict)`` fires once per evaluated iteration
      (used by the curator to publish ``civ.evolution.critique_iteration``).
    """
    first_text = str(generator(None) or "")
    if judge is None:
        return first_text, []

    iterations = max(1, int(max_iterations))
    best_text = first_text
    best_score = -1
    history: List[TraceIteration] = []
    feedback: Optional[Dict[str, Any]] = None

    for i in range(iterations):
        if i == 0:
            text = first_text
        else:
            text = str(generator(feedback) or "")

        verdict = evaluate_candidate(text, criteria=thresholds, judge=judge)
        trace = TraceIteration(
            iteration=i,
            text_hash=text_hash(text),
            verdict=verdict.to_dict(),
            refined=feedback is not None,
        )
        history.append(trace)
        if on_iteration is not None:
            try:
                on_iteration(trace, verdict)
            except Exception as exc:  # noqa: BLE001 — hook must never break the loop
                logger.warning("critique on_iteration hook failed: %s", exc)

        if verdict.score_sum > best_score:
            best_score = verdict.score_sum
            best_text = text

        if verdict.accepted:
            break

        feedback = {
            "iteration": i,
            "feedback": verdict.feedback,
            "verdict": verdict.to_dict(),
        }

    return best_text, history


def format_critique_history(history: List[TraceIteration]) -> str:
    """Compact, raw-text-free metadata line for proposal rationale.

    ``critique_history: i0 <hash12> c2/cl1/co2 no; i1 <hash12> c3/cl2/co3 ok``
    — hashes + verdicts only (bounded size; no untrusted judge text embedded).
    """
    if not history:
        return ""
    parts = []
    for t in history:
        v = t.verdict or {}
        parts.append(
            f"i{t.iteration} {t.text_hash[:12]} "
            f"c{v.get('correctness', 0)}/cl{v.get('clarity', 0)}/co{v.get('completeness', 0)} "
            f"{'ok' if v.get('accepted') else 'no'}"
        )
    return "critique_history: " + "; ".join(parts)


# ---------------------------------------------------------------------------
# Production judge: LLM-backed, via the EXISTING auxiliary client stack
# (same lazy-import pattern as hermes.platform.council.promotion_executor —
# no new HTTP client, task-resolved model config).
# ---------------------------------------------------------------------------

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_critique_json(content: str) -> Optional[Dict[str, Any]]:
    """Tolerant extraction of a JSON object from LLM text (None if absent).

    Handles fenced ```json blocks and prose-wrapped objects. Returns None on
    failure — the caller (judge) then yields a malformed verdict (fail-closed).
    """
    if not content:
        return None
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    m = _JSON_OBJECT_RE.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def build_llm_critique_judge(
    task: str = CRITIQUE_LLM_TASK,
    max_tokens: int = 512,
    temperature: float = 0.0,
    timeout: float = 60.0,
    llm_call: Optional[Callable[..., Any]] = None,
) -> Callable[[str], Dict[str, Any]]:
    """Build a judge callable backed by ``agent.auxiliary_client.call_llm``.

    ``llm_call`` is injectable for integration harnesses; tests must NOT hit a
    live model (determinism) — inject fakes into ``evaluate_candidate`` /
    ``critique_refine_loop`` directly. The judge returns the parsed dict (or
    ``{}`` on any failure) — ``evaluate_candidate`` does the fail-closed
    validation, so this function never decides acceptance itself.
    """
    def judge(text: str) -> Dict[str, Any]:
        if llm_call is not None:
            call = llm_call
        else:
            from agent.auxiliary_client import call_llm as call  # lazy: keeps import cheap
        from agent.auxiliary_client import extract_content_or_reasoning

        system = (
            "You are a strict evaluator of HAOS identity-evolution proposal "
            "rationales. Respond with ONLY a JSON object, no prose, keys: "
            '"correctness" (int 0-3), "clarity" (int 0-3), "completeness" '
            '(int 0-3), "feedback" (string, concrete improvements).'
        )
        user = f"Evaluate this candidate text:\n\n{text}"
        response = call(
            task=task,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            reasoning_config={"enabled": False},
        )
        content = extract_content_or_reasoning(response)
        parsed = parse_critique_json(content)
        if parsed is None:
            logger.warning("evolution_critique judge returned unparseable output; fail-closed.")
            return {}
        return parsed

    return judge


__all__ = [
    "CritiqueVerdict",
    "TraceIteration",
    "evaluate_candidate",
    "critique_refine_loop",
    "format_critique_history",
    "build_llm_critique_judge",
    "parse_critique_json",
    "resolve_thresholds",
    "text_hash",
    "EVENT_CRITIQUE_ITERATION",
    "DEFAULT_CRITIQUE_THRESHOLDS",
    "SCORE_DIMENSIONS",
    "CRITIQUE_LLM_TASK",
]
