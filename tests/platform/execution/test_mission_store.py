import tempfile
import time
from pathlib import Path
import pytest

from hermes.platform.execution.mission_store import MissionStore


def test_mission_store_isolation_and_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "kanban.db"
        store_a = MissionStore(db_path, profile_key="profile_a", home_path=str(Path(tmpdir) / "home_a"))
        store_b = MissionStore(db_path, profile_key="profile_b", home_path=str(Path(tmpdir) / "home_b"))

        # Profile A creates a mission
        m1 = store_a.create_or_import("m-1", title="Mission 1", objective="Test objective")
        assert m1["mission_id"] == "m-1"
        assert m1["version"] == 0
        assert m1["desired_state"] == "planned"

        # Profile B cannot create or import over m-1
        with pytest.raises(PermissionError, match="mission belongs to another profile"):
            store_b.create_or_import("m-1", title="Hijack")

        # Profile B cannot get or start Profile A's mission
        with pytest.raises(PermissionError):
            store_b.get("m-1")

        with pytest.raises(PermissionError):
            store_b.start("m-1")

        # Profile A starts mission -> version 1
        m1_running = store_a.start("m-1")
        assert m1_running["desired_state"] == "running"
        assert m1_running["actual_state"] == "running"
        assert m1_running["version"] == 1

        # Idempotent start: calling start again with no state change returns same row, version remains 1
        m1_running_again = store_a.start("m-1")
        assert m1_running_again["version"] == 1

        # Version conflict check
        with pytest.raises(RuntimeError, match="version conflict"):
            store_a.set_desired_state("m-1", "paused", expected_version=999)

        # Successful CAS
        m1_paused = store_a.set_desired_state("m-1", "paused", expected_version=1)
        assert m1_paused["desired_state"] == "paused"
        assert m1_paused["version"] == 2


def test_mission_store_approval_gates_and_expiry():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "kanban.db"
        store_a = MissionStore(db_path, profile_key="profile_a", home_path=str(Path(tmpdir) / "home_a"))
        store_b = MissionStore(db_path, profile_key="profile_b", home_path=str(Path(tmpdir) / "home_b"))

        store_a.create_or_import("m-2", title="Mission 2")

        # Request gate
        gate = store_a.request_gate(
            "m-2",
            kind="tool_execution",
            action="deploy_prod",
            idempotency_key="gate-idem-1",
        )
        gate_id = gate["gate_id"]
        assert gate["status"] == "pending"
        assert gate["action"] == "deploy_prod"

        # Idempotent request gate with same key
        gate_dup = store_a.request_gate(
            "m-2",
            kind="tool_execution",
            action="deploy_prod",
            idempotency_key="gate-idem-1",
        )
        assert gate_dup["gate_id"] == gate_id

        # Profile B cannot see pending gate
        assert store_b.list_pending_gates() == []
        assert len(store_a.list_pending_gates()) == 1

        # Profile B cannot decide gate
        with pytest.raises(KeyError, match="gate missing or access denied"):
            store_b.decide_gate(gate_id, "approved")

        # Decide gate with Profile A
        decided = store_a.decide_gate(gate_id, "approved", decided_by="council_user")
        assert decided["status"] == "approved"
        assert decided["decided_by"] == "council_user"
        assert decided["version"] == 1

        # Cannot decide twice
        with pytest.raises(RuntimeError, match="gate already approved"):
            store_a.decide_gate(gate_id, "rejected")

        # Expiry test
        expired_gate = store_a.request_gate(
            "m-2",
            kind="write_file",
            expires_at=time.time() - 10.0,  # expired in the past
        )
        with pytest.raises(RuntimeError, match="gate expired"):
            store_a.decide_gate(expired_gate["gate_id"], "approved")


def test_mission_store_usage_deduplication():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "kanban.db"
        store_a = MissionStore(db_path, profile_key="profile_a", home_path=str(Path(tmpdir) / "home_a"))
        store_a.create_or_import("m-3", title="Mission 3")

        u1 = store_a.record_usage(
            "m-3",
            task_id="t-1",
            run_id="r-1",
            provider="anthropic",
            model="claude-3-5-sonnet",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_usd=0.002,
            duration_ms=450.0,
            dedupe_key="attempt-1",
        )
        assert u1["total_tokens"] == 150
        assert u1["cost_usd"] == 0.002

        # Dedupe check
        u1_dup = store_a.record_usage(
            "m-3",
            dedupe_key="attempt-1",
            total_tokens=999,
        )
        assert u1_dup["usage_id"] == u1["usage_id"]
        assert u1_dup["total_tokens"] == 150

        # List usage
        usage_list = store_a.list_usage("m-3")
        assert len(usage_list) == 1
        assert usage_list[0]["usage_id"] == u1["usage_id"]
