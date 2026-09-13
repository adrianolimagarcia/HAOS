"""HAOS Instincts Engine: Atomic Micro-Learnings with Confidence Scoring.

Inspired by affaan-m/ECC continuous learning:
1. Micro-learnings: Atomic, single-line heuristics learned during sessions.
2. Confidence scoring: Starts low (e.g. 0.3), increases with repeated verification (+0.2),
   and decays or decreases on contradiction (-0.3).
3. Project scoping: Instincts are partitioned by project/repository to prevent cross-stack contamination.
4. Promotion to Skills: When multiple related instincts reach a confidence threshold (>= 0.8),
   the Ouroboros engine clusters and promotes them into a full reusable skill.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger("hermes.platform.memory.instincts")

CONFIDENCE_PROMOTION_THRESHOLD = 0.8
INITIAL_CONFIDENCE = 0.3
CONFIDENCE_BOOST = 0.2
CONFIDENCE_PENALTY = 0.3


@dataclass
class Instinct:
    id: str
    rule: str
    category: str  # "tool", "test", "syntax", "workflow", "domain"
    project_scope: str  # e.g. "HERMES-TURBO" or project directory hash/slug
    confidence: float = INITIAL_CONFIDENCE
    occurrences: int = 1
    created_at: int = field(default_factory=lambda: int(time.time()))
    last_validated_at: int = field(default_factory=lambda: int(time.time()))
    tags: List[str] = field(default_factory=list)
    promoted_to_skill: Optional[str] = None

    def reinforce(self) -> float:
        """Boost confidence upon positive re-occurrence."""
        self.occurrences += 1
        self.confidence = min(1.0, round(self.confidence + CONFIDENCE_BOOST, 2))
        self.last_validated_at = int(time.time())
        return self.confidence

    def penalize(self) -> float:
        """Decrease confidence on failure or contradiction."""
        self.confidence = max(0.0, round(self.confidence - CONFIDENCE_PENALTY, 2))
        self.last_validated_at = int(time.time())
        return self.confidence

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Instinct":
        return cls(**data)


class InstinctStore:
    """Persistent storage for project-scoped atomic instincts."""

    def __init__(self, root_dir: Optional[Path] = None):
        self.root = root_dir or (get_hermes_home() / "memory" / "instincts")
        self.root.mkdir(parents=True, exist_ok=True)

    def _project_file(self, project_scope: str) -> Path:
        safe_scope = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in project_scope)
        return self.root / f"{safe_scope}.json"

    def load_instincts(self, project_scope: str) -> Dict[str, Instinct]:
        pf = self._project_file(project_scope)
        if not pf.exists():
            return {}
        try:
            data = json.loads(pf.read_text(encoding="utf-8"))
            return {k: Instinct.from_dict(v) for k, v in data.items()}
        except Exception as e:
            logger.debug("Failed to load instincts for %s: %s", project_scope, e)
            return {}

    def save_instincts(self, project_scope: str, instincts: Dict[str, Instinct]) -> None:
        pf = self._project_file(project_scope)
        serialized = {k: v.to_dict() for k, v in instincts.items()}
        pf.write_text(json.dumps(serialized, indent=2, ensure_ascii=False), encoding="utf-8")

    def record_instinct(
        self,
        rule: str,
        category: str = "workflow",
        project_scope: str = "global",
        tags: Optional[List[str]] = None,
    ) -> Instinct:
        """Create or reinforce an atomic instinct."""
        instincts = self.load_instincts(project_scope)
        h = hashlib.sha256(rule.strip().lower().encode("utf-8")).hexdigest()[:12]
        instinct_id = f"ins-{h}"

        if instinct_id in instincts:
            instinct = instincts[instinct_id]
            instinct.reinforce()
            if tags:
                instinct.tags = list(set(instinct.tags + tags))
        else:
            instinct = Instinct(
                id=instinct_id,
                rule=rule.strip(),
                category=category,
                project_scope=project_scope,
                tags=tags or [],
            )
            instincts[instinct_id] = instinct

        self.save_instincts(project_scope, instincts)
        logger.info("Recorded instinct [%s] (confidence: %.2f) for %s", instinct.id, instinct.confidence, project_scope)
        return instinct

    def get_eligible_promotions(self, project_scope: str) -> List[Instinct]:
        """Return instincts with confidence >= threshold not yet promoted to skills."""
        instincts = self.load_instincts(project_scope)
        return [
            ins for ins in instincts.values()
            if ins.confidence >= CONFIDENCE_PROMOTION_THRESHOLD and not ins.promoted_to_skill
        ]

    def mark_promoted(self, project_scope: str, instinct_ids: List[str], skill_name: str) -> None:
        instincts = self.load_instincts(project_scope)
        for i_id in instinct_ids:
            if i_id in instincts:
                instincts[i_id].promoted_to_skill = skill_name
        self.save_instincts(project_scope, instincts)
