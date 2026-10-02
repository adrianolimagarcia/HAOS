"""Unit and integration tests for the HAOS Autonomous Agent Framework foundation layer."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from pathlib import Path

import pytest

from hermes.platform.agent_framework import (
    Diagnosis,
    EventSeverity,
    EvidenceItem,
    ExecutionPlan,
    ExecutionResult,
    HAOSEvent,
    HAOSEventBus,
    HAOSStateManager,
    LockTimeoutError,
    OperationalMemoryStore,
    PlanStatus,
    PlanStep,
    RiskTier,
    StepStatus,
    VerificationResult,
)


# ---------------------------------------------------------------------------
# Models & Contracts Tests
# ---------------------------------------------------------------------------

class TestModels:
    def test_event_severity_ordering(self) -> None:
        assert EventSeverity.DEBUG < EventSeverity.INFO
        assert EventSeverity.INFO < EventSeverity.WARNING
        assert EventSeverity.WARNING < EventSeverity.ERROR
        assert EventSeverity.ERROR < EventSeverity.CRITICAL
        assert EventSeverity.CRITICAL >= EventSeverity.ERROR
        assert EventSeverity.INFO <= EventSeverity.INFO

    def test_haos_event_roundtrip(self) -> None:
        event = HAOSEvent(
            event_type="disk.smart_alert",
            source="smartd",
            severity=EventSeverity.WARNING,
            payload={"dev": "nvme0n1", "reallocated_sectors": 5},
            metadata={"node": "node-alpha"},
        )
        d = event.to_dict()
        assert d["event_type"] == "disk.smart_alert"
        assert d["severity"] == "warning"
        assert d["payload"]["reallocated_sectors"] == 5

        reconstructed = HAOSEvent.from_dict(d)
        assert reconstructed.id == event.id
        assert reconstructed.event_type == event.event_type
        assert reconstructed.source == event.source
        assert reconstructed.severity == EventSeverity.WARNING
        assert reconstructed.payload == event.payload
        assert reconstructed.metadata == event.metadata

    def test_evidence_and_diagnosis_roundtrip(self) -> None:
        evidence = [
            EvidenceItem(metric="iowait", observed_value=85.5, threshold=30.0, description="Excessive disk wait"),
            EvidenceItem(metric="temperature_c", observed_value=72, threshold=70, description="Drive overheating"),
        ]
        diag = Diagnosis(
            issue_id="ISSUE-101",
            title="Drive throttling due to high temperature and iowait",
            cause="Failing fan controller on chassis slot 2",
            confidence=1.2,  # Should clamp to 1.0
            evidence=evidence,
            impacted_subsystems=["storage", "backup"],
            recommended_actions=["spin down drive", "dispatch technician"],
        )
        assert diag.confidence == 1.0

        d = diag.to_dict()
        assert d["confidence"] == 1.0
        assert len(d["evidence"]) == 2
        assert d["evidence"][0]["metric"] == "iowait"

        restored = Diagnosis.from_dict(d)
        assert restored.issue_id == "ISSUE-101"
        assert restored.confidence == 1.0
        assert len(restored.evidence) == 2
        assert restored.evidence[1].threshold == 70
        assert restored.impacted_subsystems == ["storage", "backup"]

    def test_execution_plan_and_steps(self) -> None:
        step1 = PlanStep(
            step_id="step_1",
            order=1,
            action_name="bdi_tune",
            target="/sys/block/nvme0n1/bdi/read_ahead_kb",
            params={"value": 512},
            risk_tier=RiskTier.SAFE,
            is_reversible=True,
            rollback_action="bdi_tune",
            rollback_params={"value": 128},
        )
        step2 = PlanStep(
            step_id="step_2",
            order=2,
            action_name="nvme_format",
            target="/dev/nvme0n1",
            risk_tier=RiskTier.CRITICAL,
            is_reversible=False,
        )

        plan = ExecutionPlan(
            plan_id="plan_99",
            diagnosis_id="diag_101",
            title="Storage tuning and maintenance",
            steps=[step1, step2],
            status=PlanStatus.DRAFT,
            dry_run=True,
        )

        d = plan.to_dict()
        assert d["status"] == "draft"
        assert len(d["steps"]) == 2
        assert d["steps"][0]["risk_tier"] == "safe"
        assert d["steps"][1]["risk_tier"] == "critical"

        restored = ExecutionPlan.from_dict(d)
        assert restored.plan_id == "plan_99"
        assert restored.steps[0].rollback_params == {"value": 128}
        assert restored.steps[1].risk_tier == RiskTier.CRITICAL

    def test_execution_and_verification_results(self) -> None:
        exec_res = ExecutionResult(
            step_id="step_1",
            success=True,
            output="Read-ahead set to 512kb",
            duration_ms=4.2,
        )
        d_exec = exec_res.to_dict()
        assert d_exec["success"] is True
        restored_exec = ExecutionResult.from_dict(d_exec)
        assert restored_exec.duration_ms == 4.2

        ver_res = VerificationResult(
            plan_id="plan_99",
            verified=True,
            metrics_before={"latency_p99_ms": 120},
            metrics_after={"latency_p99_ms": 15},
            details="Latency improved by 87.5%",
        )
        d_ver = ver_res.to_dict()
        assert d_ver["verified"] is True
        restored_ver = VerificationResult.from_dict(d_ver)
        assert restored_ver.metrics_after["latency_p99_ms"] == 15


# ---------------------------------------------------------------------------
# State Manager Tests
# ---------------------------------------------------------------------------

class TestStateManager:
    def test_state_get_and_set_merge(self, tmp_path: Path) -> None:
        sm = HAOSStateManager(base_dir=tmp_path)
        assert sm.get_state() == {}
        assert sm.get_state("non_existent") == {}

        # Set initial nested state
        sm.set_state({
            "cluster": {"status": "ok", "nodes": 3},
            "subsystems": {"storage": "healthy"},
        })
        assert sm.get_state("cluster") == {"status": "ok", "nodes": 3}

        # Merge update
        sm.set_state({
            "cluster": {"nodes": 4, "leader": "node1"},
            "subsystems": {"network": "healthy"},
        }, merge=True)

        full = sm.get_state()
        assert full["cluster"]["status"] == "ok"
        assert full["cluster"]["nodes"] == 4
        assert full["cluster"]["leader"] == "node1"
        assert full["subsystems"]["storage"] == "healthy"
        assert full["subsystems"]["network"] == "healthy"

    def test_state_set_overwrite(self, tmp_path: Path) -> None:
        sm = HAOSStateManager(base_dir=tmp_path)
        sm.set_state({"a": 1, "b": 2})
        sm.set_state({"c": 3}, merge=False)
        assert sm.get_state() == {"c": 3}

    def test_checkpoints_create_list_restore(self, tmp_path: Path) -> None:
        sm = HAOSStateManager(base_dir=tmp_path)
        sm.set_state({"version": 1, "config": {"max_jobs": 10}})

        # Create checkpoint
        cp1_id = sm.create_checkpoint("v1_baseline", metadata={"operator": "admin"})
        assert "v1_baseline" in cp1_id

        # Mutate state
        sm.set_state({"version": 2, "config": {"max_jobs": 20}})
        cp2_id = sm.create_checkpoint("v2_upgraded")

        # List checkpoints (should be sorted newest first)
        cps = sm.list_checkpoints()
        assert len(cps) == 2
        assert cps[0]["checkpoint_id"] == cp2_id
        assert cps[1]["checkpoint_id"] == cp1_id
        assert cps[1]["metadata"] == {"operator": "admin"}

        # Restore v1
        ok = sm.restore_checkpoint(cp1_id)
        assert ok is True
        restored_state = sm.get_state()
        assert restored_state["version"] == 1
        assert restored_state["config"]["max_jobs"] == 10

        # Restoring non-existent checkpoint returns False
        assert sm.restore_checkpoint("invalid_id") is False

    def test_file_locking_contention_timeout(self, tmp_path: Path) -> None:
        sm1 = HAOSStateManager(base_dir=tmp_path)
        sm2 = HAOSStateManager(base_dir=tmp_path)

        with sm1.acquire_lock("resource_x", timeout_sec=2.0):
            with pytest.raises(LockTimeoutError):
                with sm2.acquire_lock("resource_x", timeout_sec=0.1):
                    pass


# ---------------------------------------------------------------------------
# Operational Memory Store Tests
# ---------------------------------------------------------------------------

class TestOperationalMemoryStore:
    def test_default_files_initialization(self, tmp_path: Path) -> None:
        store = OperationalMemoryStore(base_dir=tmp_path)
        for expected in ("incidents.md", "decisions.md", "optimizations.md", "hardware.md", "current.md"):
            p = store.memory_dir / expected
            assert p.is_file()
            assert len(p.read_text(encoding="utf-8")) > 0

    def test_record_incident_and_search(self, tmp_path: Path) -> None:
        store = OperationalMemoryStore(base_dir=tmp_path)
        inc_id = store.record_incident(
            title="ZFS Pool Degraded",
            problem="vdev mirror-0 disk failure",
            diagnosis="Bad sector count exceeded on sda",
            actions=["offline sda", "attach sdc", "start resilver"],
            result="Resilver initiated successfully",
            metadata={"pool": "rpool", "failed_device": "/dev/sda"},
        )
        assert inc_id.startswith("INC-")

        # Search memory
        matches = store.search_memory("incidents.md", "resilver")
        assert len(matches) == 1
        assert inc_id in matches[0]
        assert "vdev mirror-0" in matches[0]

        # Case-insensitive search
        matches_case = store.search_memory("incidents.md", "ZFS POOL")
        assert len(matches_case) == 1

        # Search non-matching term
        assert store.search_memory("incidents.md", "non_existent_term_404") == []

    def test_record_decision_and_optimization(self, tmp_path: Path) -> None:
        store = OperationalMemoryStore(base_dir=tmp_path)
        dec_id = store.record_decision(
            title="Adopt JSONL for bus audit log",
            rationale="Enables streaming append without rewriting large files",
            alternatives=["SQLite", "Raw YAML"],
            impact="Zero locking contention during high event volume",
        )
        assert dec_id.startswith("DEC-")

        opt_id = store.record_optimization(
            component="kernel_vm",
            parameter="dirty_ratio",
            before="20",
            after="10",
            justification="Mitigate page cache flush latency spikes",
            observed_impact="p99 write latency dropped from 350ms to 40ms",
        )
        assert opt_id.startswith("OPT-")

        assert len(store.search_memory("decisions.md", "JSONL")) == 1
        assert len(store.search_memory("optimizations.md", "dirty_ratio")) == 1

    def test_update_hardware_fact_idempotent(self, tmp_path: Path) -> None:
        store = OperationalMemoryStore(base_dir=tmp_path)
        store.update_hardware_fact("nvme0", {"driver": "nvme", "pcie_gen": 4, "temp_c": 38})
        content_1 = (store.memory_dir / "hardware.md").read_text()
        assert "**temp_c**: `38`" in content_1

        # Update same device with new facts
        store.update_hardware_fact("nvme0", {"driver": "nvme", "pcie_gen": 4, "temp_c": 44})
        content_2 = (store.memory_dir / "hardware.md").read_text()
        assert "**temp_c**: `44`" in content_2
        assert "**temp_c**: `38`" not in content_2
        # Ensure device section is not duplicated
        assert content_2.count("### Device: nvme0") == 1

    def test_update_current_summary_and_get_context(self, tmp_path: Path) -> None:
        store = OperationalMemoryStore(base_dir=tmp_path)
        store.update_current_summary(
            summary="All nodes synchronized and healthy.",
            active_tasks=["Rebalance storage pool", "Daily backup"],
            issues=["Minor packet drops on eth1"],
        )
        store.record_incident(
            title="Brief eth1 link flap",
            problem="Cable re-seated",
            diagnosis="Physical connection wiggle",
            actions=["Reconnected securely"],
            result="Resolved",
        )
        store.record_optimization(
            component="tcp",
            parameter="congestion_control",
            before="cubic",
            after="bbr",
            justification="Low buffer bloat",
        )

        ctx = store.get_recent_context(max_chars=3000)
        assert "All nodes synchronized and healthy" in ctx
        assert "Rebalance storage pool" in ctx
        assert "Minor packet drops on eth1" in ctx
        assert "Brief eth1 link flap" in ctx
        assert "congestion_control" in ctx

        # Test context truncation when max_chars is small
        short_ctx = store.get_recent_context(max_chars=400)
        assert len(short_ctx) <= 450


# ---------------------------------------------------------------------------
# Event Bus Tests
# ---------------------------------------------------------------------------

class TestEventBus:
    def test_wildcard_and_exact_subscriptions(self, tmp_path: Path) -> None:
        bus = HAOSEventBus(log_dir=tmp_path)
        disk_events: list[HAOSEvent] = []
        all_events: list[HAOSEvent] = []
        exact_events: list[HAOSEvent] = []

        bus.subscribe("disk.*", lambda e: disk_events.append(e))
        bus.subscribe("*", lambda e: all_events.append(e))
        sub_exact = bus.subscribe("network.down", lambda e: exact_events.append(e))

        bus.publish(HAOSEvent(event_type="disk.read_error", source="kernel"))
        bus.publish(HAOSEvent(event_type="network.down", source="daemon"))
        bus.publish(HAOSEvent(event_type="auth.login", source="ssh"))

        assert len(disk_events) == 1
        assert disk_events[0].event_type == "disk.read_error"
        assert len(exact_events) == 1
        assert exact_events[0].event_type == "network.down"
        assert len(all_events) == 3

        # Unsubscribe exact
        assert bus.unsubscribe(sub_exact) is True
        assert bus.unsubscribe("invalid_sub_id") is False

        bus.publish(HAOSEvent(event_type="network.down", source="daemon"))
        assert len(exact_events) == 1  # No new event received
        assert len(all_events) == 4

    def test_subscriber_exception_isolation(self, tmp_path: Path) -> None:
        bus = HAOSEventBus(log_dir=tmp_path)
        successful: list[HAOSEvent] = []

        def failing_handler(e: HAOSEvent) -> None:
            raise RuntimeError("Handler exploded")

        bus.subscribe("test.*", failing_handler)
        bus.subscribe("test.*", lambda e: successful.append(e))

        # Publishing should NOT raise exception despite failing_handler
        bus.publish(HAOSEvent(event_type="test.ping", source="unit_test"))
        assert len(successful) == 1

    def test_history_and_severity_filtering(self, tmp_path: Path) -> None:
        bus = HAOSEventBus(log_dir=tmp_path, max_history=5)
        for i in range(10):
            bus.publish(HAOSEvent(
                event_type=f"metric.cpu_{i}",
                source="test",
                severity=EventSeverity.DEBUG if i < 5 else EventSeverity.ERROR,
            ))

        # History bounded to 5
        recent = bus.get_recent_events(limit=10)
        assert len(recent) == 5
        # Newest first
        assert recent[0].event_type == "metric.cpu_9"
        assert recent[-1].event_type == "metric.cpu_5"

        # Severity filter
        errors = bus.get_recent_events(min_severity=EventSeverity.ERROR)
        assert all(e.severity >= EventSeverity.ERROR for e in errors)

        # Prefix filter
        cpu_events = bus.get_recent_events(event_type_prefix="metric.cpu_")
        assert len(cpu_events) == 5

        # Clear history
        bus.clear_history()
        assert bus.get_recent_events() == []

    def test_jsonl_log_persistence(self, tmp_path: Path) -> None:
        bus = HAOSEventBus(log_dir=tmp_path)
        evt1 = HAOSEvent(event_type="sys.boot", source="init", severity=EventSeverity.INFO)
        evt2 = HAOSEvent(event_type="sys.shutdown", source="init", severity=EventSeverity.WARNING)

        bus.publish(evt1)
        bus.publish(evt2)

        log_file = bus.events_log_file
        assert log_file.is_file()

        lines = [json.loads(line) for line in log_file.read_text(encoding="utf-8").strip().splitlines()]
        assert len(lines) == 2
        assert lines[0]["event_type"] == "sys.boot"
        assert lines[1]["event_type"] == "sys.shutdown"


# ---------------------------------------------------------------------------
# Concurrency & Multi-threading Integration Tests
# ---------------------------------------------------------------------------

class TestConcurrency:
    def test_concurrent_event_bus_publishing(self, tmp_path: Path) -> None:
        bus = HAOSEventBus(log_dir=tmp_path, max_history=1000)
        received_count = 0
        lock = threading.Lock()

        def counter(e: HAOSEvent) -> None:
            nonlocal received_count
            with lock:
                received_count += 1

        bus.subscribe("worker.*", counter)

        threads: list[threading.Thread] = []
        events_per_thread = 20
        num_threads = 5

        def worker_fn(worker_id: int) -> None:
            for j in range(events_per_thread):
                bus.publish(HAOSEvent(
                    event_type=f"worker.job_{worker_id}",
                    source=f"thread_{worker_id}",
                    payload={"index": j},
                ))

        for i in range(num_threads):
            t = threading.Thread(target=worker_fn, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        assert received_count == num_threads * events_per_thread
        # Verify JSONL lines count matches
        lines = bus.events_log_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == num_threads * events_per_thread

    def test_concurrent_memory_store_appends(self, tmp_path: Path) -> None:
        store = OperationalMemoryStore(base_dir=tmp_path)
        threads: list[threading.Thread] = []
        incidents_per_thread = 5
        num_threads = 4

        def worker_fn(worker_id: int) -> None:
            for j in range(incidents_per_thread):
                store.record_incident(
                    title=f"Incident from worker {worker_id} job {j}",
                    problem="Test problem",
                    diagnosis="Test diagnosis",
                    actions=["action 1", "action 2"],
                    result="Fixed",
                )

        for i in range(num_threads):
            t = threading.Thread(target=worker_fn, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Check that incidents.md contains all records without corruption
        content = (store.memory_dir / "incidents.md").read_text(encoding="utf-8")
        assert content.count("## [INC-") == num_threads * incidents_per_thread


# ---------------------------------------------------------------------------
# Edge Cases & Recovery Tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_default_path_resolution(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        fake_home = tmp_path / "fake_home"
        monkeypatch.setenv("HERMES_HOME", str(fake_home))

        sm = HAOSStateManager()
        assert sm.base_dir == (fake_home / "agent").resolve()

        mem = OperationalMemoryStore()
        assert mem.base_dir == (fake_home / "agent").resolve()

        bus = HAOSEventBus()
        assert bus.log_dir == (fake_home / "agent").resolve()

    def test_checkpoint_collision_handling(self, tmp_path: Path) -> None:
        sm = HAOSStateManager(base_dir=tmp_path)
        sm.set_state({"key": "val1"})
        cp1 = sm.create_checkpoint("rapid_test")
        sm.set_state({"key": "val2"})
        cp2 = sm.create_checkpoint("rapid_test")
        # Collisions should result in unique IDs
        assert cp1 != cp2
        assert len(sm.list_checkpoints()) == 2

    def test_corrupted_checkpoint_recovery(self, tmp_path: Path) -> None:
        sm = HAOSStateManager(base_dir=tmp_path)
        corrupted = sm.checkpoints_dir / "bad_checkpoint.json"
        corrupted.write_text("NOT_VALID_JSON{{{", encoding="utf-8")

        # Listing should not crash
        cps = sm.list_checkpoints()
        assert any(c["checkpoint_id"] == "bad_checkpoint" for c in cps)

        # Restoring corrupted checkpoint should return False gracefully
        assert sm.restore_checkpoint("bad_checkpoint") is False

    def test_empty_search_and_nonexistent_files(self, tmp_path: Path) -> None:
        store = OperationalMemoryStore(base_dir=tmp_path)
        assert store.search_memory("nonexistent.md", "anything") == []
        assert store.search_memory("incidents.md", "") == []

    def test_plan_step_string_enum_coercion(self) -> None:
        step = PlanStep(
            step_id="step_str",
            order=1,
            action_name="test",
            target="target",
            risk_tier="moderate",  # type: ignore[arg-type]
            status="running",  # type: ignore[arg-type]
        )
        assert step.risk_tier == RiskTier.MODERATE
        assert step.status == StepStatus.RUNNING

    def test_diagnosis_empty_and_extremes(self) -> None:
        diag = Diagnosis(
            issue_id="ISSUE-000",
            title="Empty diag",
            cause="Unknown",
            confidence=-0.5,
        )
        assert diag.confidence == 0.0
        d = diag.to_dict()
        assert d["evidence"] == []
        assert d["impacted_subsystems"] == []
        assert d["recommended_actions"] == []


class TestPersistenceSafety:
    @pytest.mark.parametrize("raw", ["", "- list\n", "broken: [", "null\n"])
    def test_corrupt_state_never_becomes_empty_merge(self, tmp_path, raw):
        manager = HAOSStateManager(tmp_path)
        manager._current_state_file.write_text(raw)
        with pytest.raises(ValueError):
            manager.set_state({"new": 1})
        assert manager._current_state_file.read_text() == raw

    @pytest.mark.parametrize("filename", ["../escape", "/absolute", "sub/file", "sub\\file", "valid.md"])
    def test_restore_prevalidates_entire_manifest(self, tmp_path, filename):
        manager = HAOSStateManager(tmp_path / "agent")
        manager.set_state({"version": "live"})
        auxiliary = manager.state_dir / "aux.md"
        auxiliary.write_text("live auxiliary")
        outside = tmp_path / "outside"
        outside.write_text("outside")
        if filename == "valid.md":
            (manager.state_dir / filename).symlink_to(outside)
        checkpoint = {"state": {"version": "saved"}, "files": {"aux.md": "saved auxiliary", filename: "unsafe"}}
        (manager.checkpoints_dir / "unsafe.json").write_text(json.dumps(checkpoint))
        assert manager.restore_checkpoint("unsafe") is False
        assert manager.get_state() == {"version": "live"}
        assert auxiliary.read_text() == "live auxiliary"
        assert outside.read_text() == "outside"

    def test_checkpoint_id_and_checkpoint_symlink_rejected(self, tmp_path):
        manager = HAOSStateManager(tmp_path / "agent")
        external = tmp_path / "outside.json"
        external.write_text(json.dumps({"state": {"escaped": True}}))
        assert manager.restore_checkpoint(str(external)[:-5]) is False
        assert manager.restore_checkpoint("../../outside") is False
        (manager.checkpoints_dir / "link.json").symlink_to(external)
        assert manager.restore_checkpoint("link") is False
        assert manager.get_state() == {}

    def test_concurrent_checkpoint_ids_with_frozen_clock(self, tmp_path, monkeypatch):
        monkeypatch.setattr("hermes.platform.agent_framework.state_manager.time.time", lambda: 123.0)
        manager = HAOSStateManager(tmp_path)
        manager.set_state({"preserved": True})
        with ThreadPoolExecutor(max_workers=4) as executor:
            ids = list(executor.map(lambda _: HAOSStateManager(tmp_path).create_checkpoint("same"), range(12)))
        assert len(set(ids)) == len(ids)
        assert {entry["checkpoint_id"] for entry in manager.list_checkpoints()} == set(ids)

    @pytest.mark.parametrize("constructor", [HAOSStateManager, OperationalMemoryStore, HAOSEventBus])
    def test_profile_resolution_failure_propagates(self, tmp_path, monkeypatch, constructor):
        def fail():
            raise RuntimeError("unbound profile")
        monkeypatch.setattr("hermes_constants.get_hermes_home", fail)
        with pytest.raises(RuntimeError, match="unbound profile"):
            constructor()

    def test_profile_a_b_a_binding(self, tmp_path, monkeypatch):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "launch"))
        for profile, value in (("a", 1), ("b", 2), ("a", 3)):
            home_override = set_hermes_home_override(tmp_path / profile)
            try:
                manager = HAOSStateManager()
                if profile == "a" and value == 3:
                    assert manager.get_state() == {"value": 1}
                manager.set_state({"value": value})
                OperationalMemoryStore().record_decision(profile, str(value))
                HAOSEventBus().publish(HAOSEvent(event_type=profile, source="test"))
            finally:
                reset_hermes_home_override(home_override)
        assert not (tmp_path / "launch").exists()
        assert "**Rationale**: 2" in (tmp_path / "b" / "agent" / "memory" / "decisions.md").read_text()
        assert "**Rationale**: 1" not in (tmp_path / "b" / "agent" / "memory" / "decisions.md").read_text()
        assert len((tmp_path / "a" / "agent" / "agent-events.jsonl").read_text().splitlines()) == 2
        assert len((tmp_path / "b" / "agent" / "agent-events.jsonl").read_text().splitlines()) == 1

    def test_memory_traversal_symlink_and_corruption_rejected(self, tmp_path):
        store = OperationalMemoryStore(tmp_path / "agent")
        outside = tmp_path / "private.md"
        outside.write_text("private")
        with pytest.raises(ValueError):
            store.search_memory("../../private.md", "private")
        incident_file = store.memory_dir / "incidents.md"
        incident_file.unlink()
        incident_file.symlink_to(outside)
        with pytest.raises(ValueError):
            store.search_memory("incidents", "private")
        incident_file.unlink()
        incident_file.write_bytes(b"\xff")
        with pytest.raises(UnicodeDecodeError):
            store.record_incident("title", "problem", "diagnosis", [], "result")
        assert incident_file.read_bytes() == b"\xff"

    def test_audit_failure_prevents_dispatch_and_history(self, tmp_path, monkeypatch):
        bus = HAOSEventBus(tmp_path)
        received = []
        bus.subscribe("*", received.append)
        def fail_fsync(fd):
            raise OSError("disk failure")
        monkeypatch.setattr(os, "fsync", fail_fsync)
        with pytest.raises(OSError, match="disk failure"):
            bus.publish(HAOSEvent("test", "test"))
        assert received == []
        assert bus.get_recent_events() == []

    def test_event_log_symlink_rejected(self, tmp_path):
        bus = HAOSEventBus(tmp_path / "agent")
        outside = tmp_path / "outside"
        outside.write_text("unchanged")
        bus.events_log_file.symlink_to(outside)
        with pytest.raises(ValueError):
            bus.publish(HAOSEvent("test", "test"))
        assert outside.read_text() == "unchanged"

    def test_invalid_state_input_and_durable_private_replacement(self, tmp_path, monkeypatch):
        manager = HAOSStateManager(tmp_path)
        with pytest.raises(ValueError):
            manager.set_state([("key", "value")], merge=False)
        fsynced = []
        original_fsync = os.fsync
        def observe_fsync(fd):
            fsynced.append(os.fstat(fd).st_mode)
            original_fsync(fd)
        monkeypatch.setattr(os, "fsync", observe_fsync)
        manager.set_state({"valid": True})
        assert len(fsynced) == 2  # File contents, then directory entry.
        assert manager._current_state_file.stat().st_mode & 0o777 == 0o600

    def test_nonpositive_history_limit(self, tmp_path):
        bus = HAOSEventBus(tmp_path)
        bus.publish(HAOSEvent("test", "test"))
        assert bus.get_recent_events(limit=0) == []
        assert bus.get_recent_events(limit=-1) == []

    def test_restore_auxiliary_file_uses_atomic_replace(self, tmp_path, monkeypatch):
        manager = HAOSStateManager(tmp_path)
        manager.set_state({"saved": True})
        auxiliary = manager.state_dir / "aux.md"
        auxiliary.write_text("saved auxiliary")
        checkpoint = manager.create_checkpoint("baseline")
        auxiliary.write_text("live auxiliary")
        replacements = []
        original_replace = os.replace
        def observe_replace(source, target):
            replacements.append(Path(target))
            original_replace(source, target)
        monkeypatch.setattr(os, "replace", observe_replace)
        assert manager.restore_checkpoint(checkpoint)
        assert auxiliary in replacements
        assert manager._current_state_file in replacements
        assert auxiliary.read_text() == "saved auxiliary"
