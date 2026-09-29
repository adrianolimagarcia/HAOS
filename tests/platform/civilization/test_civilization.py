import pytest
from hermes.platform.observability.event_store import EventStore
from hermes.platform.civilization.manager import CivilizationManager
from hermes.platform.civilization.models import (
    ConstitutionRule,
    RULE_TYPE_HARD_DENY,
    RULE_TYPE_ADVISORY,
    POLICY_RESULT_ALLOW,
    POLICY_RESULT_DENY,
    POLICY_RESULT_REQUIRE_APPROVAL,
)


def test_constitution_enactment_and_policy_gates(tmp_path):
    store = EventStore(tmp_path / "test_events.db")
    civ = CivilizationManager(store)

    rules = [
        ConstitutionRule(
            id="rule-no-shell-leak",
            name="Block Dangerous Commands",
            description="Prevent execution of dangerous shell commands",
            rule_type=RULE_TYPE_HARD_DENY,
            target_action="exec_danger_shell",
        ),
        ConstitutionRule(
            id="rule-external-api-advisory",
            name="Warn External API",
            description="Require confirmation when invoking paid external APIs",
            rule_type=RULE_TYPE_ADVISORY,
            target_action="invoke_paid_api",
        ),
    ]

    cv = civ.enact_constitution(
        version=1,
        title="HAOS Autonomous Civilization Constitution",
        rules=rules,
        approved_by="council-founding",
    )
    assert cv.version == 1
    assert len(cv.rules) == 2

    # Hard deny check
    d1 = civ.evaluate_policy("bot-worker", "exec_danger_shell", "rm -rf /")
    assert d1.result == POLICY_RESULT_DENY
    assert d1.rule_id == "rule-no-shell-leak"

    # Advisory check
    d2 = civ.evaluate_policy("bot-worker", "invoke_paid_api", "anthropic:opus")
    assert d2.result == POLICY_RESULT_REQUIRE_APPROVAL
    assert d2.rule_id == "rule-external-api-advisory"

    # Allow unconstrained action
    d3 = civ.evaluate_policy("bot-worker", "read_file", "data.txt")
    assert d3.result == POLICY_RESULT_ALLOW
    assert d3.rule_id is None


def test_civilization_memory_assertions(tmp_path):
    store = EventStore(tmp_path / "test_events.db")
    civ = CivilizationManager(store)

    a1 = civ.record_assertion(
        subject="bot-coder",
        predicate="expert_in",
        object_="rust_simd",
        confidence=0.98,
        provenance_ref="benchmark-20250929",
    )
    assert a1.id.startswith("assert-")

    a2 = civ.record_assertion(
        subject="bot-writer",
        predicate="expert_in",
        object_="technical_docs",
        confidence=0.95,
        provenance_ref="pr-104",
    )

    coder_assertions = civ.query_knowledge(subject="bot-coder")
    assert len(coder_assertions) == 1
    assert coder_assertions[0].object == "rust_simd"

    expert_assertions = civ.query_knowledge(predicate="expert_in")
    assert len(expert_assertions) == 2
