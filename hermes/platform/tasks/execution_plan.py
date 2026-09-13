from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

@dataclass
class DelegationPlan:
    kind: str # capability | child_task
    target: str # ex: "image-understanding" ou "T-183"
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ExecutionPlan:
    task_id: str
    approach_summary: str
    steps: List[str] = field(default_factory=list)
    delegations: List[Dict[str, Any]] = field(default_factory=list)
    revision: int = 1
    generated_by: str = "architect"
    current_step_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionPlan":
        return cls(
            task_id=data["task_id"],
            approach_summary=data.get("approach_summary", ""),
            steps=list(data.get("steps") or []),
            delegations=list(data.get("delegations") or []),
            revision=int(data.get("revision", 1)),
            generated_by=data.get("generated_by", "architect"),
            current_step_index=int(data.get("current_step_index", 0)),
            metadata=dict(data.get("metadata") or {}),
        )

    def advance_step(self) -> Optional[str]:
        if self.current_step_index < len(self.steps):
            step = self.steps[self.current_step_index]
            self.current_step_index += 1
            return step
        return None
