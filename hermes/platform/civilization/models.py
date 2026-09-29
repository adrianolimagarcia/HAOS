"""Domain models for HAOS Civilization V4 Core.

Defines Constitution rules, versions, policy decisions,
and civilization shared memory assertions.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

RULE_TYPE_HARD_DENY = "hard_deny"
RULE_TYPE_ADVISORY = "advisory"

POLICY_RESULT_ALLOW = "allow"
POLICY_RESULT_DENY = "deny"
POLICY_RESULT_REQUIRE_APPROVAL = "require_approval"

EVENT_CONSTITUTION_ENACTED = "civ.constitution.enacted"
EVENT_POLICY_EVALUATED = "civ.policy.evaluated"
EVENT_ASSERTION_RECORDED = "civ.memory.assertion_recorded"


@dataclass
class ConstitutionRule:
    id: str
    name: str
    description: str
    rule_type: str
    target_action: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConstitutionRule:
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            rule_type=data.get("rule_type", RULE_TYPE_HARD_DENY),
            target_action=data.get("target_action", "*"),
        )


@dataclass
class ConstitutionVersion:
    version: int
    title: str
    rules: List[ConstitutionRule]
    approved_by: str
    effective_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "title": self.title,
            "rules": [r.to_dict() for r in self.rules],
            "approved_by": self.approved_by,
            "effective_at": self.effective_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConstitutionVersion:
        return cls(
            version=int(data["version"]),
            title=data["title"],
            rules=[ConstitutionRule.from_dict(r) for r in data.get("rules", [])],
            approved_by=data["approved_by"],
            effective_at=float(data.get("effective_at", time.time())),
        )


@dataclass
class PolicyDecision:
    id: str
    rule_id: Optional[str]
    subject_bot: str
    action: str
    resource: str
    result: str
    reason: str
    evaluated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PolicyDecision:
        return cls(
            id=data["id"],
            rule_id=data.get("rule_id"),
            subject_bot=data["subject_bot"],
            action=data["action"],
            resource=data["resource"],
            result=data.get("result", POLICY_RESULT_ALLOW),
            reason=data.get("reason", ""),
            evaluated_at=float(data.get("evaluated_at", time.time())),
        )


@dataclass
class KnowledgeAssertion:
    id: str
    subject: str
    predicate: str
    object: str
    confidence: float
    provenance_ref: str
    valid_from: float = field(default_factory=time.time)
    valid_to: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> KnowledgeAssertion:
        return cls(
            id=data["id"],
            subject=data["subject"],
            predicate=data["predicate"],
            object=data["object"],
            confidence=float(data.get("confidence", 1.0)),
            provenance_ref=data["provenance_ref"],
            valid_from=float(data.get("valid_from", time.time())),
            valid_to=data.get("valid_to"),
        )
