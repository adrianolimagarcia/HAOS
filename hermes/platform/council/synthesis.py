"""Synthesis engine and consensus deliberation for Council positions."""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("hermes.platform.council.synthesis")

# Verdict marker consumed by the debate runner's human-approval gate (C1).
BLOCKED_MARKER = "BLOCKED"


def _is_blocked_verdict(decision: str) -> bool:
    return BLOCKED_MARKER in (decision or "").upper()


def _extract_vote(position: Any) -> str:
    """Normalize a member position into a comparable vote token for majority tallying (C2)."""
    if isinstance(position, dict):
        for key in ("vote", "decision", "answer", "position"):
            if key in position:
                return str(position[key]).strip().lower()
        return json.dumps(position, sort_keys=True, default=str).lower()
    return str(position).strip().lower()


@dataclass
class SynthesisResult:
    decision: str
    synthesis: str
    confidence: float = 1.0
    dissent: Dict[str, str] = field(default_factory=dict)
    action_plan: List[Dict[str, Any]] = field(default_factory=list)
    needs_human_approval: bool = False
    tokens_used: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SynthesisBot:
    """Produces structured synthesis and binding decision from member inputs."""

    def __init__(self, synthesizer_fn: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None):
        self.synthesizer_fn = synthesizer_fn

    def synthesize(
        self,
        council_id: str,
        session_id: str,
        objective: str,
        positions: Dict[str, Any],
        dissent_map: Dict[str, str],
        debate_turns: Optional[List[Dict[str, Any]]] = None,
        decision_mode: str = "consensus_with_dissent",
    ) -> SynthesisResult:
        """Synthesize positions into a coherent decision record, preserving dissent."""
        if not positions:
            raise ValueError(f"Cannot synthesize decision for session {session_id}: no positions available")

        # If custom model/LLM synthesizer is supplied, invoke it
        if self.synthesizer_fn:
            payload = {
                "council_id": council_id,
                "session_id": session_id,
                "objective": objective,
                "positions": positions,
                "dissent": dissent_map,
                "debate_turns": debate_turns or [],
                "decision_mode": decision_mode,
            }
            raw = self.synthesizer_fn(f"Synthesize council deliberation for '{objective}'", payload)
            return SynthesisResult(
                decision=raw.get("decision", f"Council resolved objective: {objective}"),
                synthesis=raw.get("synthesis", "Deliberation synthesized across all member positions."),
                confidence=float(raw.get("confidence", 0.9)),
                dissent=dict(raw.get("dissent", dissent_map)),
                action_plan=list(raw.get("action_plan", [])),
                needs_human_approval=bool(raw.get("needs_human_approval", False)),
                tokens_used=int(raw.get("tokens_used", 100)),
                cost_usd=float(raw.get("cost_usd", 0.001)),
            )

        # Deterministic rule-based synthesis fallback
        participant_count = len(positions)
        dissent_count = len(dissent_map)

        summary_lines = [
            f"Deliberation concluded under mode '{decision_mode}'.",
            f"Total participants: {participant_count}; Registered dissents: {dissent_count}.",
            "Consolidated views:",
        ]
        for bot_id, pos in positions.items():
            summary_lines.append(f"- {bot_id}: {pos}")

        if dissent_map:
            summary_lines.append("Dissenting arguments:")
            for bot_id, reason in dissent_map.items():
                summary_lines.append(f"- {bot_id}: {reason}")

        synthesis_text = "\n".join(summary_lines)

        # Calculate decision per configured decision_mode (C2).
        if decision_mode == "majority":
            # Real vote tally over explicit positions: strict majority of votes wins.
            votes = Counter(_extract_vote(pos) for pos in positions.values())
            total = sum(votes.values())
            top_vote, top_count = votes.most_common(1)[0]
            tied = sum(1 for c in votes.values() if c == top_count) > 1
            if not tied and top_count > total / 2:
                decision = f"APPROVED by majority ({top_count}/{total} votes): {objective}"
                confidence = 0.75 + 0.20 * (top_count / total)
            else:
                decision = f"DIVIDED / BLOCKED: no majority among {total} votes: {objective}"
                confidence = 0.50
        elif decision_mode == "single_synthesizer":
            # The synthesizer decides alone; dissent is recorded but cannot block.
            decision = f"APPROVED by single synthesizer: {objective}"
            confidence = 0.90 if dissent_count == 0 else 0.70
        else:
            # consensus_with_dissent (default): dissent < half of participants approves.
            if dissent_count == 0:
                decision = f"APPROVED by full consensus: {objective}"
                confidence = 0.95
            elif dissent_count < participant_count / 2:
                decision = f"APPROVED with recorded dissent: {objective}"
                confidence = 0.80
            else:
                decision = f"DIVIDED / BLOCKED due to substantial dissent: {objective}"
                confidence = 0.50

        # Action plan derived from positions
        action_plan = [
            {
                "action": "execute_objective",
                "resource": council_id,
                "objective": objective,
                "risk_class": "low" if dissent_count == 0 else "medium",
            }
        ]
        needs_human = dissent_count > 0 or "production" in objective.lower()

        return SynthesisResult(
            decision=decision,
            synthesis=synthesis_text,
            confidence=confidence,
            dissent=dict(dissent_map),
            action_plan=action_plan,
            needs_human_approval=needs_human,
            tokens_used=80,
            cost_usd=0.0008,
        )


__all__ = ["SynthesisBot", "SynthesisResult"]
