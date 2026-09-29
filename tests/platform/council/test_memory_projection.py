"""Unit tests for Council MEMORY.md projection engine."""

import tempfile
from pathlib import Path

import pytest

from hermes.platform.bots.identity import CouncilSpec
from hermes.platform.council.manager import CouncilManager
from hermes.platform.council.memory_projection import CouncilMemoryProjectionEngine
from hermes.platform.observability.event_store import EventStore


@pytest.fixture
def projection_env():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        store = EventStore(root / "events.db")
        council_mgr = CouncilManager(store)
        output_dir = root / "councils"

        engine = CouncilMemoryProjectionEngine(
            event_store=store,
            output_dir=output_dir,
            profile_id="test-profile",
        )

        yield {
            "root": root,
            "store": store,
            "council_mgr": council_mgr,
            "engine": engine,
            "output_dir": output_dir,
        }


def test_council_memory_projection_deterministic(projection_env):
    council_mgr = projection_env["council_mgr"]
    engine = projection_env["engine"]

    # Register council
    spec = CouncilSpec(
        id="devops-council",
        purpose="Platform stability and deployment automation",
        members=["infra-bot", "deploy-bot"],
        roles={"infra-bot": "Infrastructure Lead", "deploy-bot": "Release Manager"},
        decision_mode="consensus_with_dissent",
    )
    council_mgr.register(spec)

    # Start session and record a decision
    session = council_mgr.start_session(
        council_id="devops-council",
        objective="Automate canary rollback triggers",
    )
    council_mgr.submit_position(session.session_id, "infra-bot", "Approve rollback triggers")
    council_mgr.submit_position(session.session_id, "deploy-bot", "Approve rollback triggers")
    council_mgr.record_decision(
        session_id=session.session_id,
        synthesis="Unanimously approved automated canary rollbacks on error threshold.",
        decision="Adopt Canary Policy v1",
        confidence=0.98,
        action_refs=["deploy_canary_monitor"],
    )

    # Project memory
    record = engine.project_council("devops-council")
    assert record.council_id == "devops-council"
    assert record.decisions_count == 1
    assert Path(record.file_path).exists()

    content = Path(record.file_path).read_text(encoding="utf-8")
    assert "# Council Memory: devops-council" in content
    assert "Platform stability and deployment automation" in content
    assert "Adopt Canary Policy v1" in content
    assert "infra-bot" in content
    assert "deploy-bot" in content


def test_council_memory_projection_rebuild_idempotency(projection_env):
    council_mgr = projection_env["council_mgr"]
    engine = projection_env["engine"]

    spec = CouncilSpec(
        id="core-council",
        purpose="Core platform governance",
        members=["core-bot-1", "core-bot-2"],
    )
    council_mgr.register(spec)

    rec1 = engine.project_council("core-council")
    content1 = Path(rec1.file_path).read_text(encoding="utf-8")

    # Re-project
    rec2 = engine.project_council("core-council")
    content2 = Path(rec2.file_path).read_text(encoding="utf-8")

    # Should have identical contents
    assert content1 == content2
    assert rec1.content_hash == rec2.content_hash


def test_council_memory_projection_preserves_operator_override(projection_env):
    council_mgr = projection_env["council_mgr"]
    engine = projection_env["engine"]

    spec = CouncilSpec(
        id="ops-council",
        purpose="Operations council",
        members=["ops-bot"],
    )
    council_mgr.register(spec)

    rec1 = engine.project_council("ops-council")
    file_path = Path(rec1.file_path)

    # Operator manually appends notes to MEMORY.md
    manual_note = "\n## 4. Operator Overrides & Manual Annotations\n- Critical Override: All deployments frozen on Fridays.\n\n---"
    file_path.write_text(file_path.read_text(encoding="utf-8") + manual_note, encoding="utf-8")

    # Re-project without force rebuild
    rec2 = engine.project_council("ops-council", force_full_rebuild=False)
    assert rec2.has_operator_override is True

    new_content = file_path.read_text(encoding="utf-8")
    assert "All deployments frozen on Fridays" in new_content
