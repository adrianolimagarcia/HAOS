"""Runner determinístico para cenários de avaliação do HAOS (Fase 7).

Executa a stack real do HAOS contra um modelo fake que replaya respostas e fatos,
sem custos de API e com garantia de repetibilidade.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from evals.harness.schema import AssertionTarget, EvalAssertion, EvalScenario, EvalTurn
from hermes.platform.context.memory.candidate import MemoryCandidate
from hermes.platform.context.memory.classifier import AssertionType
from hermes.platform.context.memory.evidence import (
    EvidenceRecord,
    EvidenceRegistry,
    EvidenceStatus,
)
from hermes.platform.context.memory.router import (
    STAGE_REASON_BLOCKED_ASSERTION_TYPE,
    STAGE_REASON_CONFLICT,
    STAGE_REASON_LOW_CONFIDENCE,
    STAGE_REASON_MISSING_EVIDENCE,
    MemoryRouter,
)
from hermes.platform.context.memory.staging import MemoryStagingStore


@dataclass
class AssertionResult:
    target: str
    expected: Any
    actual: Any
    passed: bool
    description: Optional[str] = None


@dataclass
class EvalResult:
    scenario_name: str
    passed: bool
    total_assertions: int
    passed_assertions: int
    assertion_results: List[AssertionResult] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_name": self.scenario_name,
            "passed": self.passed,
            "total_assertions": self.total_assertions,
            "passed_assertions": self.passed_assertions,
            "assertion_results": [
                {
                    "target": r.target,
                    "expected": r.expected,
                    "actual": r.actual,
                    "passed": r.passed,
                    "description": r.description,
                }
                for r in self.assertion_results
            ],
            "metadata": self.metadata,
        }


class MockMemoryProvider:
    """Provedor em memória para coletar fatos consolidados."""

    def __init__(self, initial_state: Optional[Dict[str, Any]] = None):
        self.state: Dict[str, List[str]] = initial_state or {}
        self.consolidated: List[MemoryCandidate] = []

    def get_existing_facts(self, destination: str) -> List[str]:
        return self.state.get(destination, [])

    def append_block(self, destination: str, content: str) -> bool:
        self.state.setdefault(destination, []).append(content)
        return True

    def write_candidate(self, candidate: MemoryCandidate) -> bool:
        self.consolidated.append(candidate)
        self.state.setdefault(candidate.proposed_destination, []).append(candidate.fact)
        return True

    def consolidate(self, candidate: MemoryCandidate) -> bool:
        return self.write_candidate(candidate)


class EvalRunner:
    """Executa cenários puros de avaliação contra a stack real do HAOS."""

    def __init__(self, tmp_dir: Optional[Path] = None):
        self.tmp_dir = tmp_dir

    def run_scenario(self, scenario: EvalScenario) -> EvalResult:
        import tempfile

        # 1. Configura stack real em memória/isolada
        evidence_registry = EvidenceRegistry()
        evidence_registry.add_trusted_authority("test_harness")
        staging_dir = self.tmp_dir or Path(tempfile.mkdtemp(prefix="eval_staging_"))
        staging_store = MemoryStagingStore(root_dir=staging_dir)
        router = MemoryRouter(
            staging_store=staging_store,
            verifier=evidence_registry,
        )
        provider = MockMemoryProvider(initial_state=scenario.initial_state.copy())

        last_candidate: Optional[MemoryCandidate] = None
        last_evidence_status: Optional[str] = None
        last_staged_reason: Optional[str] = None
        is_promoted: bool = False

        # 2. Executa cada turno
        for turn in scenario.turns:
            fact_text = turn.extracted_fact or turn.user_input
            source_uri = turn.source_uri or ""

            # Se o turno fornece atestação de evidência, registra formalmente no EvidenceRegistry
            if turn.evidence_attestation and source_uri:
                status_str = turn.evidence_attestation.get("status", "VERIFIED")
                rec = evidence_registry.register(
                    uri=source_uri,
                    status=EvidenceStatus(status_str),
                    verifier=turn.evidence_attestation.get("verifier", "test_harness"),
                    digest=turn.evidence_attestation.get("digest", "hash_digest_12345678"),
                    attestation=turn.evidence_attestation,
                    supports_claim=turn.evidence_attestation.get("supports_claim", True),
                )
                last_evidence_status = rec.status.value

            # Roteia o fato na stack real
            candidate = router.route_fact(
                fact=fact_text,
                source_uri=source_uri,
                confidence=turn.confidence,
            )
            last_candidate = candidate

            # Processa o candidato
            promoted = router.process_candidate(candidate, provider)
            is_promoted = promoted

            # Se foi colocado em staging, identifica a razão
            if not promoted and candidate.status == "pending":
                staged_records = staging_store.load()
                for rec_val in reversed(list(staged_records.values())):
                    if rec_val.get("fact") == candidate.fact:
                        last_staged_reason = rec_val.get("gate") or rec_val.get("reason")
                        break

        # 3. Avalia asserções
        assertion_results: List[AssertionResult] = []
        for ass in scenario.assertions:
            actual: Any = None
            passed: bool = False

            if ass.target == AssertionTarget.GBRAIN_GATE:
                if last_candidate and last_candidate.classification:
                    actual = last_candidate.classification.assertion_type.value
                else:
                    actual = "UNKNOWN"
                passed = actual == ass.expected_value

            elif ass.target == AssertionTarget.CANDIDATE_STATUS:
                if is_promoted:
                    actual = "promoted"
                else:
                    actual = last_candidate.status if last_candidate else "none"
                passed = actual == ass.expected_value

            elif ass.target == AssertionTarget.STAGING_REASON:
                actual = last_staged_reason or "none"
                passed = actual == ass.expected_value

            elif ass.target == AssertionTarget.EVIDENCE_STATUS:
                actual = last_evidence_status or "none"
                passed = actual == ass.expected_value

            elif ass.target == AssertionTarget.DESTINATION:
                actual = last_candidate.proposed_destination if last_candidate else "none"
                passed = actual == ass.expected_value

            elif ass.target == AssertionTarget.ROUTER:
                actual = "promoted" if is_promoted else "staged"
                passed = actual == ass.expected_value

            assertion_results.append(
                AssertionResult(
                    target=ass.target.value,
                    expected=ass.expected_value,
                    actual=actual,
                    passed=passed,
                    description=ass.description,
                )
            )

        passed_count = sum(1 for r in assertion_results if r.passed)
        all_passed = passed_count == len(assertion_results)

        return EvalResult(
            scenario_name=scenario.name,
            passed=all_passed,
            total_assertions=len(assertion_results),
            passed_assertions=passed_count,
            assertion_results=assertion_results,
            metadata={"description": scenario.description, "tags": scenario.tags},
        )

    def run_scenario_file(self, file_path: Union[str, Path]) -> EvalResult:
        p = Path(file_path)
        content = json.loads(p.read_text(encoding="utf-8"))
        scenario = EvalScenario.model_validate(content)
        return self.run_scenario(scenario)
