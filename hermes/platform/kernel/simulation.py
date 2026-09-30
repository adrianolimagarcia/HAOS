"""Simulation Mode Engine.

Provides zero-mutation dry-run simulation of tasks and changes:
- Evaluates problem understanding and breaks down execution steps
- Leverages ProjectDigitalTwin to compute blast radius and dependent modules
- Estimates token consumption and USD cost
- Evaluates policy constraints, risk tier, and governance gates
- Produces a comprehensive SimulationReport without executing side effects.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from hermes.platform.kernel.contract import AgentRole, AgentRuntimeContract, ToolRiskTier
from hermes.platform.kernel.digital_twin import BlastRadius, ProjectDigitalTwin


@dataclass
class SimulationReport:
    simulation_id: str
    task_prompt: str
    predicted_steps: List[str]
    blast_radius: BlastRadius
    estimated_tokens: int
    estimated_cost_usd: float
    risk_level: str  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    policy_status: str  # "APPROVED_FOR_AUTONOMY", "REQUIRES_COUNCIL_RATIFICATION", "REQUIRES_HUMAN_GATE"
    duration_ms: float
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "task_prompt": self.task_prompt,
            "predicted_steps": list(self.predicted_steps),
            "blast_radius": self.blast_radius.to_dict(),
            "estimated_tokens": self.estimated_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "risk_level": self.risk_level,
            "policy_status": self.policy_status,
            "duration_ms": round(self.duration_ms, 2),
            "created_at": self.created_at,
        }


class SimulationEngine:
    """Engine orchestrating dry-run simulations."""

    def __init__(self, digital_twin: Optional[ProjectDigitalTwin] = None):
        self.digital_twin = digital_twin or ProjectDigitalTwin()
        if not self.digital_twin.dependencies:
            self.digital_twin.scan_repository(max_files=300)

    def simulate_task(
        self,
        task_prompt: str,
        target_files: Optional[List[str]] = None,
        complexity_hint: str = "medium",
    ) -> SimulationReport:
        """Run dry-run simulation without modifying the environment."""
        start_time = time.time()
        sim_id = f"sim-{uuid.uuid4().hex[:10]}"

        # Infer target files from prompt if not explicitly given
        inferred_targets = list(target_files or [])
        if not inferred_targets:
            # Simple heuristic detection of file paths mentioned in prompt
            words = task_prompt.split()
            for w in words:
                clean_w = w.strip("`'\"(),:;")
                if "/" in clean_w or clean_w.endswith(".py") or clean_w.endswith(".ts"):
                    inferred_targets.append(clean_w)

        if not inferred_targets:
            inferred_targets = ["hermes/platform/kernel/"]

        blast = self.digital_twin.predict_blast_radius(inferred_targets)

        # Plan synthesis
        predicted_steps = [
            f"1. Understand requirements and bounded scope for: {task_prompt[:60]}...",
            f"2. Inspect target codebase: {', '.join(blast.target_files[:3])}",
            f"3. Draft deterministic patch isolating dependencies ({len(blast.affected_dependents)} dependents)",
            f"4. Run validation suite covering associated tests ({len(blast.associated_tests)} tests)",
            "5. Submit patch to Critic Agent for independent verification",
            "6. Promote validated solution into Project Memory",
        ]

        # Token & cost estimations
        base_tokens_per_file = 2500
        estimated_tokens = (
            5000 +  # planning & analysis
            len(blast.target_files) * base_tokens_per_file +
            len(blast.associated_tests) * 1500
        )
        # Assuming ~$0.005 per 1k tokens blended
        estimated_cost = (estimated_tokens / 1000.0) * 0.005

        # Risk classification
        if blast.risk_score >= 0.80:
            risk_level = "CRITICAL"
            policy_status = "REQUIRES_COUNCIL_RATIFICATION"
        elif blast.risk_score >= 0.50:
            risk_level = "HIGH"
            policy_status = "REQUIRES_COUNCIL_RATIFICATION" if blast.requires_council_approval else "REQUIRES_HUMAN_GATE"
        elif blast.risk_score >= 0.25:
            risk_level = "MEDIUM"
            policy_status = "APPROVED_FOR_AUTONOMY"
        else:
            risk_level = "LOW"
            policy_status = "APPROVED_FOR_AUTONOMY"

        duration_ms = (time.time() - start_time) * 1000.0

        return SimulationReport(
            simulation_id=sim_id,
            task_prompt=task_prompt,
            predicted_steps=predicted_steps,
            blast_radius=blast,
            estimated_tokens=estimated_tokens,
            estimated_cost_usd=estimated_cost,
            risk_level=risk_level,
            policy_status=policy_status,
            duration_ms=duration_ms,
        )

    def simulate(self, task_prompt: str, target_files: Optional[List[str]] = None, complexity_hint: str = "medium", **kwargs) -> SimulationReport:
        return self.simulate_task(task_prompt=task_prompt, target_files=target_files, complexity_hint=complexity_hint)


SimulationModeEngine = SimulationEngine
