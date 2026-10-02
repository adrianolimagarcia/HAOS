"""Opt-in, profile-bound durable investigation runtime (no model-selected actions)."""
from __future__ import annotations

import fcntl
import hashlib
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from hermes_constants import get_hermes_home, set_hermes_home_override, reset_hermes_home_override
from .autonomy_config import AutonomyConfig
from .autonomy_sources import AutonomySources
from .event_queue import EventQueue
from .models import Diagnosis, HAOSEvent
from .pipeline import HAOSOrchestrator, ObserverAgent, DiagnosticianAgent, PlannerAgent
from .policy import AgentPolicyEngine
from .state_manager import HAOSStateManager


class _Snapshot:
    def __init__(self, value):
        self.value = value

    def observe(self):
        return self.value

    def diagnose(self, telemetry):
        return self.value


class AutonomyService:
    def __init__(self, base_dir=None, *, investigator=None, observer=None, config=None):
        self.base_dir = Path(base_dir if base_dir is not None else get_hermes_home() / "agent").absolute()
        HAOSStateManager._check_path(self.base_dir)
        self._config_override = config
        self._closed = False
        self._stop = threading.Event()
        self._tick_lock = threading.Lock()
        self._heartbeat_lock = threading.Lock()
        self.active_job_id = None
        self.last_error = None
        self.status = "starting"
        self._lease = None
        self._lease_lost = False
        self._last_telemetry = None
        self._telemetry = {}
        self._lock_fd = None
        self.queue = None
        with self._scope():
            cfg = self._config()
            self.state = HAOSStateManager(self.base_dir)
            directory = self.base_dir / "autonomy"
            self.state._check_path(directory / "service.lock")
            directory.mkdir(parents=True, exist_ok=True)
            self._lock_fd = os.open(directory / "service.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(self._lock_fd)
                self._lock_fd = None
                raise RuntimeError("Autonomy service already running") from None
            try:
                self.queue = EventQueue(self.base_dir, max_pending=cfg.max_pending,
                                        cooldown_seconds=cfg.cooldown_seconds)
                if investigator is None:
                    from .investigator import Investigator
                    investigator = Investigator(timeout=cfg.investigation_timeout,
                                                max_output_tokens=cfg.max_output_tokens)
                self.investigator = investigator
                self.observer = observer or ObserverAgent(wal_paths=[self.base_dir.parent / "state.db-wal"])
                self.sources = AutonomySources(self.base_dir)
                self._heartbeat()
            except BaseException:
                if self.queue:
                    self.queue.close()
                os.close(self._lock_fd)
                self._lock_fd = None
                raise
        # This thread reads no ambient profile/config: all paths and leases are explicit.
        self._thread = threading.Thread(target=self._pulse, name="autonomy-heartbeat", daemon=True)
        self._thread.start()

    @contextmanager
    def _scope(self):
        scope_handle = set_hermes_home_override(self.base_dir.parent)
        try:
            yield
        finally:
            reset_hermes_home_override(scope_handle)

    def _config(self):
        return self._config_override or AutonomyConfig.load()

    def _allowed(self):
        return not self._stop.is_set() and self._config().enabled and not self.queue.get_pause()

    def _heartbeat(self):
        with self._heartbeat_lock:
            self.state._write_json_file_atomic(self.base_dir / "autonomy" / "service.json", {
                "status": self.status, "updated_at": time.time(), "pid": os.getpid(),
                "active_job_id": self.active_job_id, "last_error": self.last_error})

    def _pulse(self):
        while not self._stop.wait(2):
            try:
                with self._scope():
                    lease = self._lease
                    if lease and not self.queue.renew(*lease):
                        self._lease_lost = True
                        self._cancel()
                    self._heartbeat()
            except Exception as exc:
                self.last_error = type(exc).__name__
                self._lease_lost = True
                self._cancel()

    def _cancel(self):
        cancel = getattr(self.investigator, "cancel", None)
        if cancel is not None:
            cancel()

    @staticmethod
    def _diagnoses(result, event):
        # Stable issue identity lets an operator grant this EXACT report, not a future wildcard.
        identity = str(event.get("payload", {}).get("issue_id") or event.get("payload", {}).get("issue") or
                       (event.get("event_type", "") + ":" + event.get("source", "")))
        issue = hashlib.sha256(identity.encode()).hexdigest()[:24]
        # Only text becomes diagnoses; model output can never choose a handler/target.
        diagnoses = []
        for index, finding in enumerate(result.get("findings", [])[:4]):
            recommendations = [str(r)[:256] for r in finding.get("recommendations", [])[:4]]
            diagnoses.append(Diagnosis(f"issue_{issue}_{index}", str(finding.get("title", ""))[:256],
                                       str(finding.get("cause", ""))[:512], finding.get("confidence", 0),
                                       recommended_actions=recommendations))
        return diagnoses

    def tick(self):
        if self._closed:
            raise RuntimeError("Service closed")
        if not self._tick_lock.acquire(blocking=False):
            raise RuntimeError("Tick already running")
        try:
            with self._scope():
                return self._tick()
        finally:
            self._tick_lock.release()

    def _tick(self):
        job = None
        mutation_started = False
        self.last_error = None
        try:
            cfg = self._config()
            if not self._allowed():
                self.status = "paused" if cfg.enabled else "disabled"
                self._heartbeat()
                return {"status": self.status}
            self.status = "idle"
            ingestion = self.sources.ingest(self.queue, cfg)
            now = time.monotonic()
            if self._last_telemetry is None or now - self._last_telemetry >= cfg.telemetry_seconds:
                self._telemetry = self.observer.observe()
                for diagnosis in DiagnosticianAgent().diagnose(self._telemetry):
                    self.queue.submit(HAOSEvent("telemetry.warning", "trusted_telemetry", severity="warning",
                                               payload={"issue_id": diagnosis.issue_id, "title": diagnosis.title}))
                self._last_telemetry = now
            if not self._allowed():
                self.status = "paused"
                return {"status": self.status, "ingestion": ingestion}
            lease_seconds = cfg.investigation_timeout + 120
            job = self.queue.claim(f"service_{os.getpid()}", lease_seconds=lease_seconds,
                                   max_jobs_per_hour=cfg.max_jobs_per_hour)
            if not job:
                return {"status": "idle", "ingestion": ingestion}
            self.active_job_id = job["job_id"]
            self._lease_lost = False
            self._lease = (job["job_id"], job["owner"], lease_seconds)
            self.status = "investigating"
            self._heartbeat()
            # Recovery cannot establish whether a previous handler crossed its side-effect boundary.
            if job["recovered"]:
                self.queue.finish(job["job_id"], job["owner"], "uncertain", {"outcome": "recovered_requires_review"})
                return {"status": "uncertain", "job_id": job["job_id"]}
            investigation = self.investigator.investigate(job["event"], self._telemetry)
            diagnoses = self._diagnoses(investigation, job["event"])
            orchestrator = HAOSOrchestrator(self.base_dir, observer=_Snapshot(self._telemetry),
                                           diagnostician=_Snapshot(diagnoses),
                                           planner=PlannerAgent(report_target=self.base_dir / "artifacts" / "operational-report.json"))
            record = orchestrator.run_cycle(dry_run=True)
            plan = orchestrator.load_plan(record["plan"]["plan_id"])
            outcome = "pending_authorization"
            # Re-read both opt-in and policy at the mutation boundary; never issue a grant.
            policy = AgentPolicyEngine(self.base_dir / "agent-policy.yaml")
            if (self._allowed() and self._config().auto_apply and not self._lease_lost and
                    all(policy.action_grant(step, autonomous=True) for step in plan.steps)):
                if not self.queue.renew(job["job_id"], job["owner"], lease_seconds):
                    self._lease_lost = True
                else:
                    self.state._write_json_file_atomic(self.base_dir / "autonomy" / "mutation-intent.json",
                                                       {"job_id": job["job_id"], "plan_id": plan.plan_id})
                    mutation_started = True
                    if self._allowed() and not self._lease_lost:
                        orchestrator.executor.mutation_guard = lambda: (
                            self._allowed() and self._config().auto_apply and not self._lease_lost and
                            self.queue.renew(job["job_id"], job["owner"], lease_seconds))
                        record = orchestrator.run_cycle(mode="autonomous", plan=plan)
                        outcome = record["outcome"]
            result = {"outcome": outcome, "plan_id": plan.plan_id,
                      "summary": str(investigation.get("summary", ""))[:1024],
                      "findings": [d.to_dict() for d in diagnoses],
                      "usage": investigation.get("usage", {}),
                      "model": str(investigation.get("model", ""))[:128],
                      "provider": str(investigation.get("provider", ""))[:128],
                      "cycle_id": record["cycle_id"],
                      "verification": record["verification"]}
            status = "uncertain" if self._lease_lost else "completed"
            if not self.queue.finish(job["job_id"], job["owner"], status, result):
                status = "uncertain"
                result["outcome"] = "lease_lost_requires_review"
            return {"status": status, "job_id": job["job_id"], **result}
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.status = "error"
            status = "uncertain" if mutation_started or self._lease_lost else "failed"
            if job:
                self.queue.finish(job["job_id"], job["owner"], status, {"error": self.last_error})
            return {"status": status, "error": self.last_error}
        finally:
            self._lease = None
            self.active_job_id = None
            if self.status == "investigating":
                self.status = "idle"
            self._heartbeat()

    def run(self, stop_event=None):
        stop_event = stop_event or threading.Event()
        def watch():
            while not self._stop.wait(0.1):
                if stop_event.is_set():
                    self._stop.set()
                    self._cancel()
                    return
        watcher = threading.Thread(target=watch, name="autonomy-stop", daemon=True)
        watcher.start()
        try:
            while not stop_event.is_set() and not self._stop.is_set():
                self.tick()
                with self._scope():
                    interval = self._config().poll_seconds
                stop_event.wait(interval)
        finally:
            self._stop.set()
            watcher.join(timeout=2)
            self.close()

    def close(self):
        if self._closed:
            return
        self._stop.set()
        self._cancel()
        self._thread.join(timeout=3)
        with self._tick_lock:
            self.status = "stopped"
            with self._scope():
                self._heartbeat()
                self.queue.close()
            os.close(self._lock_fd)
            self._lock_fd = None
            self._closed = True
