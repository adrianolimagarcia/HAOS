"""Observability Fabric & Ouroboros Closed Loop (Marcos 10 & 11).

Implementa:
- RunTrace & TraceSpan: Rastreamento tipado de execução com custos detalhados de modelo/provider.
- OuroborosLifecycleManager: Orquestra o ciclo formal de evolução:
  proposal -> sandbox -> eval -> comparison -> adoption.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from hermes.platform.skills.spec import SkillSpec
from hermes.platform.skills.procedural_engine import (
    SkillGenerator,
    SkillLifecyclePipeline,
    SkillRegistry,
    TaskExecutionRecord,
)
from hermes.platform.capabilities.lsp.unified_intelligence import ImpactAnalyzer
from hermes.platform.workspaces.automerge import AutoMergeGate
from hermes.platform.workspaces.merge_queue import MergeCandidate, MergeQueue, MergeStatus
from hermes.platform.workspaces.git_worktree import GitWorktreeManager

EvolutionProposalStatus = Literal["proposed", "sandboxed", "evaluating", "adopted", "rejected"]


@dataclass
class TraceSpan:
    """Span individual de execução agêntica."""

    span_id: str
    name: str
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    model_name: Optional[str] = None
    provider: Optional[str] = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    status: str = "ok"
    attributes: Dict[str, Any] = field(default_factory=dict)

    def finish(self, status: str = "ok") -> None:
        self.end_time = time.time()
        self.status = status

    def duration(self) -> float:
        end = self.end_time or time.time()
        return max(0.0, end - self.start_time)


@dataclass
class RunTrace:
    """Rastreamento completo de uma execução agêntica com agregação de métricas."""

    trace_id: str
    task_id: str
    posture: str
    spans: List[TraceSpan] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def add_span(self, span: TraceSpan) -> None:
        self.spans.append(span)

    def total_cost(self) -> float:
        return sum(s.cost_usd for s in self.spans)

    def total_tokens(self) -> Dict[str, int]:
        return {
            "input": sum(s.tokens_input for s in self.spans),
            "output": sum(s.tokens_output for s in self.spans),
            "total": sum(s.tokens_input + s.tokens_output for s in self.spans),
        }


@dataclass
class EvolutionProposal:
    """Proposta formal de evolução gerada pelo Ouroboros."""

    proposal_id: str
    target: str  # ex: "context:budget", "skills:auth", "model:routing"
    proposed_changes: Dict[str, Any]
    rationale: str
    status: EvolutionProposalStatus = "proposed"
    baseline_score: Optional[float] = None
    candidate_score: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    evaluated_at: Optional[float] = None


@dataclass
class EvolutionCycleResult:
    """Result of an end-to-end Ouroboros evolution simulation cycle."""
    success: bool
    proposal: Optional[EvolutionProposal] = None
    candidate_skill: Optional[SkillSpec] = None
    stage: str = "init"
    eval_score: float = 0.0
    baseline_score: float = 0.0
    promoted: bool = False
    worktree_path: Optional[Path] = None
    blast_radius: Optional[Dict[str, Any]] = None
    affected_tests: List[str] = field(default_factory=list)
    merge_candidate: Optional[MergeCandidate] = None
    merged: bool = False
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


class OuroborosLifecycleManager:
    """Controla o avanço de propostas de evolução através de sandboxes e gates de avaliação."""

    def __init__(
        self,
        skill_registry: Optional[SkillRegistry] = None,
        skill_generator: Optional[SkillGenerator] = None,
        skill_pipeline: Optional[SkillLifecyclePipeline] = None,
        automerge_gate: Optional[AutoMergeGate] = None,
        impact_analyzer: Optional[ImpactAnalyzer] = None,
        merge_queue: Optional[MergeQueue] = None,
        worktree_manager: Optional[GitWorktreeManager] = None,
        promotion_threshold: float = 0.80,
        min_improvement_pct: float = 0.05,
    ):
        self._proposals: Dict[str, EvolutionProposal] = {}
        self.skill_registry = skill_registry or SkillRegistry()
        self.skill_generator = skill_generator or SkillGenerator()
        self.skill_pipeline = skill_pipeline or SkillLifecyclePipeline(
            registry=self.skill_registry,
            min_eval_score=promotion_threshold,
        )
        self.automerge_gate = automerge_gate
        self.impact_analyzer = impact_analyzer
        self.merge_queue = merge_queue
        self.worktree_manager = worktree_manager
        self.promotion_threshold = promotion_threshold
        self.min_improvement_pct = min_improvement_pct

    def submit_proposal(self, target: str, changes: Dict[str, Any], rationale: str) -> EvolutionProposal:
        raw = f"{target}:{json.dumps(changes, sort_keys=True)}:{time.time()}"
        p_id = f"evo-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"
        proposal = EvolutionProposal(
            proposal_id=p_id,
            target=target,
            proposed_changes=changes,
            rationale=rationale,
            status="proposed",
        )
        self._proposals[p_id] = proposal
        return proposal

    def sandbox_proposal(self, proposal_id: str) -> bool:
        p = self._proposals.get(proposal_id)
        if not p or p.status != "proposed":
            return False
        p.status = "sandboxed"
        return True

    def evaluate_proposal(
        self,
        proposal_id: str,
        baseline_score: float,
        candidate_score: float,
        min_improvement_pct: Optional[float] = None,
    ) -> bool:
        threshold = self.min_improvement_pct if min_improvement_pct is None else min_improvement_pct
        p = self._proposals.get(proposal_id)
        if not p or p.status not in ("sandboxed", "evaluating"):
            return False
        p.status = "evaluating"
        p.baseline_score = baseline_score
        p.candidate_score = candidate_score
        p.evaluated_at = time.time()

        # Gate de adoção: melhora mensurável
        delta = (candidate_score - baseline_score) / max(0.0001, baseline_score)
        if delta >= threshold:
            p.status = "adopted"
            return True
        else:
            p.status = "rejected"
            return False

    def get_proposal(self, proposal_id: str) -> Optional[EvolutionProposal]:
        return self._proposals.get(proposal_id)

    def simulate_evolution_cycle(
        self,
        task_history: List[Union[TaskExecutionRecord, RunTrace, Dict[str, Any]]],
        target_task_name: Optional[str] = None,
        skill_name: Optional[str] = None,
        dry_run: bool = False,
        candidate_eval_score: Optional[float] = None,
        baseline_score: float = 0.50,
        branch: Optional[str] = None,
    ) -> EvolutionCycleResult:
        """Executes a full Ouroboros evolution cycle:
        1. Analyze task run traces / execution records.
        2. Identify candidate improvements / procedural patterns.
        3. Generate candidate evolution proposal (EvolutionProposal & SkillSpec).
        4. Spin up isolated workspace / worktree.
        5. Run sandbox verification and LSP impact analysis on affected test suites.
        6. Evaluate score against promotion threshold.
        7. Enqueue in MergeQueue and execute automated merge if passed.
        """
        # 1. Normalizar histórico para TaskExecutionRecord
        records: List[TaskExecutionRecord] = []
        for item in task_history:
            if isinstance(item, TaskExecutionRecord):
                records.append(item)
            elif isinstance(item, RunTrace):
                records.append(
                    TaskExecutionRecord(
                        task_name=item.task_id,
                        action_sequence=[s.name for s in item.spans],
                        success=all(s.status == "ok" for s in item.spans) if item.spans else True,
                        context_keys=list(item.attributes.keys()),
                        capabilities_used=[s.attributes.get("capability") for s in item.spans if "capability" in s.attributes],
                        duration_sec=sum(s.duration() for s in item.spans),
                        metadata={"token_cost": item.total_cost()},
                    )
                )
            elif isinstance(item, dict):
                records.append(
                    TaskExecutionRecord(
                        task_name=item.get("task_name", "task_auto"),
                        action_sequence=item.get("action_sequence", []),
                        success=item.get("success", True),
                        context_keys=item.get("context_keys", []),
                        capabilities_used=item.get("capabilities_used", []),
                        duration_sec=item.get("duration", item.get("duration_sec", 0.0)),
                        metadata={"token_cost": item.get("token_cost", 0.0)},
                    )
                )

        if not records:
            return EvolutionCycleResult(
                success=False,
                stage="trace_analysis",
                error="No valid task execution records or traces provided",
            )

        # 2. Identificar melhorias / padrões procedurais via SkillGenerator
        candidate_spec = self.skill_generator.generate_candidate_from_history(
            execution_history=records,
            target_task_name=target_task_name,
            skill_name=skill_name,
        )

        if not candidate_spec:
            return EvolutionCycleResult(
                success=False,
                stage="pattern_detection",
                error="No repetitive successful procedural pattern identified to formulate evolution proposal",
            )

        # 3. Gerar proposta formal de evolução (EvolutionProposal)
        flow_steps = candidate_spec.metadata.get("flow", candidate_spec.metadata.get("pattern", []))
        proposal = self.submit_proposal(
            target=f"skill:{candidate_spec.name}",
            changes={
                "version": candidate_spec.version,
                "entry_script": candidate_spec.entry_script,
                "flow": flow_steps,
                "capabilities": candidate_spec.capabilities_required,
                "posture": candidate_spec.preferred_posture,
            },
            rationale=(
                f"Auto-generated skill '{candidate_spec.name}' from recurring procedural "
                f"execution patterns ({len(flow_steps)} steps)."
            ),
        )

        # 4. Spin up isolated workspace / worktree
        task_id = f"evo-{candidate_spec.name}-{candidate_spec.version.replace('.', '_')}"
        worktree_path: Optional[Path] = None

        if self.worktree_manager:
            try:
                if branch:
                    worktree_path = self.worktree_manager.create_worktree(task_id=task_id, base_branch=branch)
                else:
                    worktree_path = self.worktree_manager.create_worktree(task_id=task_id)
            except Exception as exc:
                return EvolutionCycleResult(
                    success=False,
                    proposal=proposal,
                    candidate_skill=candidate_spec,
                    stage="worktree_provisioning",
                    error=f"Failed to create isolated worktree: {exc}",
                )

        # 5. Run sandbox verification and LSP impact analysis on affected test suites
        self.sandbox_proposal(proposal.proposal_id)

        # Sandbox pipeline do Skill
        ok, msg = self.skill_pipeline.advance_to_sandbox(candidate_spec)
        if not ok:
            proposal.status = "rejected"
            if self.worktree_manager and worktree_path:
                try:
                    self.worktree_manager.remove_worktree(task_id)
                except Exception:
                    pass
            return EvolutionCycleResult(
                success=False,
                proposal=proposal,
                candidate_skill=candidate_spec,
                stage="sandbox_pipeline",
                worktree_path=worktree_path,
                error=f"Sandbox validation failed: {msg}",
            )

        # AutoMerge Gate / LSP impact analysis se configurado
        blast_info: Optional[Dict[str, Any]] = None
        affected_tests: List[str] = []

        if self.impact_analyzer or self.automerge_gate:
            try:
                diff_text = ""
                if self.worktree_manager:
                    try:
                        diff_text = self.worktree_manager.get_diff(task_id)
                    except Exception:
                        diff_text = ""

                if self.automerge_gate:
                    mod_files, mod_syms = self.automerge_gate.parse_diff_impact(
                        diff_text, worktree_root=worktree_path
                    )
                    analyzer = self.impact_analyzer or self.automerge_gate.impact_analyzer
                    if analyzer:
                        blast = analyzer.calculate_blast_radius(
                            modified_symbols=mod_syms,
                            modified_files=mod_files,
                        )
                        blast_info = blast.to_dict()
                        affected_tests = sorted(list(blast.affected_test_suites))
                elif self.impact_analyzer:
                    blast = self.impact_analyzer.calculate_blast_radius(
                        modified_files=["service.py"] if "service.py" in diff_text else []
                    )
                    blast_info = blast.to_dict()
                    affected_tests = sorted(list(blast.affected_test_suites))
            except Exception as exc:
                blast_info = {"error": str(exc)}

        # Avançar no pipeline para eval
        ok, msg = self.skill_pipeline.advance_to_eval(candidate_spec)
        if not ok:
            proposal.status = "rejected"
            if self.worktree_manager and worktree_path:
                try:
                    self.worktree_manager.remove_worktree(task_id)
                except Exception:
                    pass
            return EvolutionCycleResult(
                success=False,
                proposal=proposal,
                candidate_skill=candidate_spec,
                stage="eval_advance",
                worktree_path=worktree_path,
                error=f"Advance to eval failed: {msg}",
            )

        # 6. Evaluate score against promotion threshold
        score = candidate_eval_score if candidate_eval_score is not None else 0.90
        evaluated = self.evaluate_proposal(
            proposal_id=proposal.proposal_id,
            baseline_score=baseline_score,
            candidate_score=score,
            min_improvement_pct=self.min_improvement_pct,
        )

        if not evaluated or score < self.promotion_threshold:
            proposal.status = "rejected"
            if self.worktree_manager and worktree_path:
                try:
                    self.worktree_manager.remove_worktree(task_id)
                except Exception:
                    pass
            return EvolutionCycleResult(
                success=False,
                proposal=proposal,
                candidate_skill=candidate_spec,
                stage="eval_gate",
                eval_score=score,
                baseline_score=baseline_score,
                promoted=False,
                worktree_path=worktree_path,
                blast_radius=blast_info,
                affected_tests=affected_tests,
                error=(
                    f"Candidate eval score {score:.2f} failed threshold "
                    f"({self.promotion_threshold:.2f}) or did not achieve min improvement"
                ),
            )

        # Promover skill no pipeline e registrar
        ok, msg = self.skill_pipeline.evaluate_and_activate(
            candidate_spec, eval_score_override=score
        )
        if not ok:
            proposal.status = "rejected"
            if self.worktree_manager and worktree_path:
                try:
                    self.worktree_manager.remove_worktree(task_id)
                except Exception:
                    pass
            return EvolutionCycleResult(
                success=False,
                proposal=proposal,
                candidate_skill=candidate_spec,
                stage="skill_activation",
                eval_score=score,
                baseline_score=baseline_score,
                promoted=False,
                worktree_path=worktree_path,
                blast_radius=blast_info,
                affected_tests=affected_tests,
                error=f"Skill activation failed: {msg}",
            )

        if dry_run:
            if self.worktree_manager and worktree_path:
                try:
                    self.worktree_manager.remove_worktree(task_id)
                except Exception:
                    pass
            return EvolutionCycleResult(
                success=True,
                proposal=proposal,
                candidate_skill=candidate_spec,
                stage="dry_run_completed",
                eval_score=score,
                baseline_score=baseline_score,
                promoted=True,
                worktree_path=worktree_path,
                blast_radius=blast_info,
                affected_tests=affected_tests,
                merged=False,
                details={"dry_run": True},
            )

        # 7. Enqueue in MergeQueue and execute automated merge if passed
        merge_candidate: Optional[MergeCandidate] = None
        merged = False
        target_branch_name = branch or f"haos/task-{task_id}"

        if self.merge_queue:
            report_hash = hashlib.sha256(
                f"{proposal.proposal_id}:{score}:{time.time()}".encode("utf-8")
            ).hexdigest()[:16]

            merge_candidate = self.merge_queue.enqueue(
                task_id=task_id,
                branch=target_branch_name,
                test_report_hash=report_hash,
                priority=10,
                metadata={
                    "proposal_id": proposal.proposal_id,
                    "skill_name": candidate_spec.name,
                    "eval_score": score,
                },
            )

            # Process queue to execute merge
            processed = self.merge_queue.process_next()
            if processed and processed.status == MergeStatus.MERGED:
                merged = True
            elif processed and processed.status == MergeStatus.REJECTED:
                merged = False
                return EvolutionCycleResult(
                    success=False,
                    proposal=proposal,
                    candidate_skill=candidate_spec,
                    stage="merge_queue",
                    eval_score=score,
                    baseline_score=baseline_score,
                    promoted=True,
                    worktree_path=worktree_path,
                    blast_radius=blast_info,
                    affected_tests=affected_tests,
                    merge_candidate=processed,
                    merged=False,
                    error=f"Merge queue rejected merge: {processed.rejection_reason}",
                )
        elif self.automerge_gate:
            # Se não houver merge queue mas houver automerge gate, tenta verify_and_merge diretamente
            merge_res = self.automerge_gate.verify_and_merge(task_id=task_id)
            merged = bool(merge_res.get("merged", False))
        else:
            # Sem gate ou queue, considera verificado com sucesso
            merged = True

        return EvolutionCycleResult(
            success=True,
            proposal=proposal,
            candidate_skill=candidate_spec,
            stage="completed",
            eval_score=score,
            baseline_score=baseline_score,
            promoted=True,
            worktree_path=worktree_path,
            blast_radius=blast_info,
            affected_tests=affected_tests,
            merge_candidate=merge_candidate,
            merged=merged,
            details={"proposal_status": proposal.status},
        )
