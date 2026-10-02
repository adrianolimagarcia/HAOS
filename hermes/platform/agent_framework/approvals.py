"""Expiring approvals issued by an authenticated operator surface, not plan input.

The caller of issue() is the authority boundary (CLI operator/session-authenticated
API). An approval ID alone is useless without its durable store record.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import asdict, dataclass

from .models import ExecutionPlan, PlanStep
from .state_manager import HAOSStateManager


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def plan_binding(plan: ExecutionPlan) -> str:
    # Runtime status/dry_run change during preview/apply; intent must not.
    steps = []
    for step in plan.steps:
        item = step.to_dict()
        item.pop("status")
        steps.append(item)
    intent = {"plan_id": plan.plan_id, "steps": steps}
    return hashlib.sha256(canonical(intent).encode()).hexdigest()


@dataclass(frozen=True)
class StepApproval:
    approval_id: str
    plan_digest: str
    step_id: str
    action_name: str
    target: str
    params_digest: str
    expires_at: float

    def to_dict(self):
        return asdict(self)


class ApprovalStore:
    def __init__(self, base_dir):
        self.state = HAOSStateManager(base_dir)
        self.directory = self.state.base_dir / "approvals"

    def issue(self, plan: ExecutionPlan, step: PlanStep, *, ttl_seconds: float = 300) -> StepApproval:
        if not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= 3600:
            raise ValueError("Approval TTL must be in (0, 3600]")
        if not any(item is step for item in plan.steps):
            raise ValueError("Step does not belong to plan")
        approval = StepApproval(uuid.uuid4().hex, plan_binding(plan), step.step_id,
                                step.action_name, step.target,
                                hashlib.sha256(canonical(step.params).encode()).hexdigest(),
                                time.time() + ttl_seconds)
        self.state._write_json_file_atomic(self.directory / f"{approval.approval_id}.json", approval.to_dict())
        return approval

    def matches(self, approval_ids, plan: ExecutionPlan, step: PlanStep) -> bool:
        for approval_id in approval_ids:
            if not isinstance(approval_id, str) or len(approval_id) != 32 or any(c not in "0123456789abcdef" for c in approval_id):
                continue
            path = self.directory / f"{approval_id}.json"
            self.state._check_path(path)
            try:
                data = json.loads(path.read_text())
            except FileNotFoundError:
                continue
            expected = {"approval_id": approval_id, "plan_digest": plan_binding(plan),
                        "step_id": step.step_id, "action_name": step.action_name, "target": step.target,
                        "params_digest": hashlib.sha256(canonical(step.params).encode()).hexdigest()}
            if not isinstance(data, dict):
                raise ValueError("Malformed approval record")
            expiry = data.get("expires_at")
            if (all(data.get(k) == v for k, v in expected.items())
                    and type(expiry) in (int, float) and math.isfinite(expiry) and time.time() < expiry):
                return True
        return False
