"""Execution runner for individual Council member Bots within frozen Leaf snapshots."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from hermes.platform.bots.identity import IdentityVersion
from hermes.platform.bots.leaf_protocol import LeafIdentitySnapshot, create_leaf_identity_snapshot

logger = logging.getLogger("hermes.platform.council.member_runner")


@dataclass
class MemberExecutionResult:
    bot_id: str
    leaf_id: str
    position: str
    confidence: float = 1.0
    dissent: Optional[str] = None
    evidence_refs: List[str] = field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    model: str = "default"
    latency_ms: float = 0.0
    leaf_snapshot: Optional[Dict[str, Any]] = None


LeafExecutor = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class MemberRunner:
    """Manages the isolated, frozen execution of a Bot participating in a Council."""

    def __init__(
        self,
        identity_provider: Any,
        executor: Optional[LeafExecutor] = None,
    ):
        self.identity_provider = identity_provider
        self.executor = executor or self._default_mock_executor

    def _default_mock_executor(self, prompt: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministic fallback executor when no external LLM runner is configured."""
        bot_id = context.get("bot_id", "bot")
        objective = context.get("objective", "")
        round_num = context.get("round", 0)

        pos = f"[{bot_id}] Analysis for '{objective}' (round {round_num}): Assessed feasibility and risks."
        dissent = None
        if "sec" in bot_id.lower() and "urgent" in objective.lower():
            dissent = "Security review raises concerns on rushed timeline."

        return {
            "position": pos,
            "confidence": 0.85,
            "dissent": dissent,
            "evidence_refs": [f"eval-{bot_id}-{round_num}"],
            "tokens_used": 150,
            "cost_usd": 0.0015,
            "model": "haos-council-member-v1",
        }

    def _get_identity_version(self, bot_id: str) -> IdentityVersion:
        """Resolve active IdentityVersion for bot_id."""
        prov = self.identity_provider
        if hasattr(prov, "get_active_version"):
            active = prov.get_active_version(bot_id)
            if active:
                return active
        # Fallback stub
        return IdentityVersion(
            id=f"{bot_id}-v1-fallback",
            bot_id=bot_id,
            version=1,
            bundle_hash="hash-fallback",
            status="active",
        )

    def execute_member_analysis(
        self,
        bot_id: str,
        council_id: str,
        session_id: str,
        objective: str,
        round_num: int = 0,
        debate_context: Optional[Dict[str, Any]] = None,
    ) -> MemberExecutionResult:
        """Run an isolated analysis turn for a bot, freezing its identity into a Leaf."""
        start_t = time.perf_counter()

        identity_ver = self._get_identity_version(bot_id)
        leaf_id = f"leaf-{bot_id}-{uuid.uuid4().hex[:8]}"
        model = "haos-council-member-v1"

        # Freeze execution context into Leaf snapshot (no mutating parent SOUL)
        snapshot: LeafIdentitySnapshot = create_leaf_identity_snapshot(
            leaf_id=leaf_id,
            parent_bot_id=bot_id,
            identity_version=identity_ver,
            task_description=f"Council deliberation: {objective}",
            council_id=council_id,
            council_session_id=session_id,
            model=model,
            correlation_id=session_id,
        )

        # Build prompt (sanitized; in round 0 no cross-bot positions are included)
        prompt_parts = [
            f"You are {bot_id} participating in Council '{council_id}'.",
            f"Objective: {objective}",
            f"Deliberation Round: {round_num}",
        ]
        if debate_context and debate_context.get("other_positions"):
            prompt_parts.append("\nPrevious round submissions by other council members:")
            for other_bot, other_pos in debate_context["other_positions"].items():
                if other_bot != bot_id:
                    prompt_parts.append(f"- {other_bot}: {other_pos}")
            if debate_context.get("critiques"):
                prompt_parts.append("\nPoints for debate:")
                for crit in debate_context["critiques"]:
                    prompt_parts.append(f"- {crit}")

        prompt = "\n".join(prompt_parts)

        context = {
            "bot_id": bot_id,
            "council_id": council_id,
            "session_id": session_id,
            "objective": objective,
            "round": round_num,
            "snapshot_id": snapshot.leaf_id,
            "assigned_model": model,
        }

        raw_result = self.executor(prompt, context)
        latency_ms = (time.perf_counter() - start_t) * 1000.0

        return MemberExecutionResult(
            bot_id=bot_id,
            leaf_id=snapshot.leaf_id,
            position=raw_result.get("position", ""),
            confidence=float(raw_result.get("confidence", 1.0)),
            dissent=raw_result.get("dissent"),
            evidence_refs=list(raw_result.get("evidence_refs", [])),
            tokens_used=int(raw_result.get("tokens_used", 0)),
            cost_usd=float(raw_result.get("cost_usd", 0.0)),
            model=str(raw_result.get("model", model)),
            latency_ms=latency_ms,
            leaf_snapshot=snapshot.to_dict(),
        )


__all__ = ["MemberRunner", "MemberExecutionResult", "LeafExecutor"]
