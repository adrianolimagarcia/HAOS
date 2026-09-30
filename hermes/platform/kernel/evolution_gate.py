"""Safe Evolution Gate and Ouroboros Protection Pipeline.

Guards recursive self-evolution against runaway failure loops:
Pipeline:
Mutation -> Simulation -> Evaluation -> Policy Approval -> Promotion

Ensures mutations are simulated and evaluated before any modification to
core Bot identity, rules, or system configuration is promoted.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from hermes.platform.kernel.simulation import SimulationEngine, SimulationReport


class EvolutionStage(str, Enum):
    MUTATION_PROPOSED = "mutation_proposed"
    SIMULATED = "simulated"
    SIMULATION = "simulated"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    PROMOTED = "promoted"
    PROMOTION = "promoted"
    REJECTED = "rejected"


class EvolutionGateError(Exception):
    """Raised when an evolutionary mutation fails verification gates."""
    pass


@dataclass
class MutationProposal:
    mutation_id: str = ""
    target_entity: str = ""  # "bot:<id>", "rule:<id>", "skill:<id>"
    mutation_type: str = "soul_update"  # "soul_update", "rule_change", "skill_refinement", "tool_expansion"
    diff_summary: str = ""
    rationale: str = ""
    proposed_by: str = ""
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Flexible compatibility kwargs
    proposal_id: Optional[str] = None
    target_bot_id: Optional[str] = None
    proposed_soul_delta: Optional[str] = None
    target_files: Optional[List[str]] = None
    author_agent: Optional[str] = None

    def __post_init__(self) -> None:
        if self.proposal_id and not self.mutation_id:
            self.mutation_id = self.proposal_id
        if not self.mutation_id:
            self.mutation_id = f"mut-{uuid.uuid4().hex[:8]}"
        if self.target_bot_id and not self.target_entity:
            self.target_entity = f"bot:{self.target_bot_id}"
        if self.proposed_soul_delta and not self.diff_summary:
            self.diff_summary = self.proposed_soul_delta
        if self.author_agent and not self.proposed_by:
            self.proposed_by = self.author_agent
        if self.target_files and "target_file" not in self.metadata:
            self.metadata["target_file"] = self.target_files[0] if self.target_files else ""


@dataclass
class EvolutionGateReceipt:
    proposal_id: str
    stage: EvolutionStage
    simulation_report: Optional[SimulationReport] = None
    evaluation_passed: bool = False
    approved_by: Optional[str] = None
    promotion_version_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    @property
    def promoted(self) -> bool:
        return self.stage == EvolutionStage.PROMOTED

    @property
    def rollback_triggered(self) -> bool:
        return self.stage == EvolutionStage.REJECTED

    @property
    def current_stage(self) -> EvolutionStage:
        return self.stage

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "stage": self.stage.value,
            "simulation": self.simulation_report.to_dict() if self.simulation_report else None,
            "evaluation_passed": self.evaluation_passed,
            "approved_by": self.approved_by,
            "promotion_version_id": self.promotion_version_id,
            "rejection_reason": self.rejection_reason,
            "timestamp": self.timestamp,
        }


class EvolutionGate:
    """Multi-stage gate controlling safe autonomous evolution."""

    def __init__(self, simulation_engine: Optional[SimulationEngine] = None):
        self.simulation_engine = simulation_engine or SimulationEngine()
        self._history: Dict[str, EvolutionGateReceipt] = {}

    def process_proposal(
        self,
        proposal: MutationProposal,
        eval_pass_rate: float = 1.0,
        approver: Optional[str] = None,
        is_council_ratified: bool = False,
    ) -> EvolutionGateReceipt:
        """Run proposal through the 5-stage Evolution Gate."""
        # 1. Mutation Proposed
        receipt = EvolutionGateReceipt(
            proposal_id=proposal.mutation_id,
            stage=EvolutionStage.MUTATION_PROPOSED,
        )

        # 2. Simulation
        sim_report = self.simulation_engine.simulate_task(
            task_prompt=f"Evolution mutation on {proposal.target_entity}: {proposal.diff_summary}",
            target_files=[proposal.metadata.get("target_file", "hermes/platform/evolution/")],
        )
        receipt.simulation_report = sim_report
        receipt.stage = EvolutionStage.SIMULATED

        # If simulation indicates CRITICAL risk without council ratification, reject immediately
        if sim_report.risk_level == "CRITICAL" and not is_council_ratified:
            receipt.stage = EvolutionStage.REJECTED
            receipt.rejection_reason = (
                f"Simulation identified CRITICAL blast risk ({sim_report.blast_radius.risk_score:.2f}). "
                "Council ratification is mandatory prior to promotion."
            )
            self._history[proposal.mutation_id] = receipt
            return receipt

        # 3. Evaluation
        if eval_pass_rate < 0.90:
            receipt.stage = EvolutionStage.REJECTED
            receipt.evaluation_passed = False
            receipt.rejection_reason = f"Evaluation regression pass rate ({eval_pass_rate:.1%}) below 90% threshold"
            self._history[proposal.mutation_id] = receipt
            return receipt

        receipt.evaluation_passed = True
        receipt.stage = EvolutionStage.EVALUATED

        # 4. Policy Approval
        effective_approver = approver or ("Council" if is_council_ratified else "AutoPolicyEngine")
        receipt.approved_by = effective_approver
        receipt.stage = EvolutionStage.APPROVED

        # 5. Promotion
        new_version_id = f"v-{uuid.uuid4().hex[:8]}"
        receipt.promotion_version_id = new_version_id
        receipt.stage = EvolutionStage.PROMOTED

        self._history[proposal.mutation_id] = receipt
        return receipt

    def get_receipt(self, proposal_id: str) -> Optional[EvolutionGateReceipt]:
        return self._history.get(proposal_id)

    def process_mutation(
        self,
        proposal: MutationProposal,
        evaluation_fn: Optional[Any] = None,
        policy_approval_fn: Optional[Any] = None,
        eval_pass_rate: float = 1.0,
        approver: Optional[str] = None,
        is_council_ratified: bool = False,
    ) -> EvolutionGateReceipt:
        pass_rate = eval_pass_rate
        if evaluation_fn is not None:
            pass_rate = 1.0 if evaluation_fn(proposal) else 0.5
        if policy_approval_fn is not None and not policy_approval_fn(proposal):
            receipt = EvolutionGateReceipt(
                proposal_id=proposal.mutation_id,
                stage=EvolutionStage.REJECTED,
                rejection_reason="Policy approval rejected",
            )
            self._history[proposal.mutation_id] = receipt
            return receipt
        return self.process_proposal(
            proposal=proposal,
            eval_pass_rate=pass_rate,
            approver=approver,
            is_council_ratified=is_council_ratified,
        )
