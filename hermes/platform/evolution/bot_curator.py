"""Background evolution curator for autonomous bot identity lifecycle.

Ingests ExperienceEvents from canonical EventStore, updates durable cursors,
generates EvolutionProposals, manages single-flight profile leases, and monitors canary deployments.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.bots.identity_manager import IdentityManager
from hermes.platform.council.manager import CouncilManager
from hermes.platform.evolution.bot_evolution import (
    BotEvolutionManager,
    EVENT_EXPERIENCE_RECORDED,
    ExperienceEvent,
    RISK_HIGH,
    RISK_IDENTITY_CRITICAL,
    RISK_LOW,
    RISK_MEDIUM,
    STATUS_APPROVED,
    STATUS_CANARY,
    STATUS_DRAFT,
    STATUS_PROMOTED,
    STATUS_REJECTED,
    STATUS_REVIEW,
    STATUS_ROLLED_BACK,
)
from hermes.platform.observability.event_store import EventStore

logger = logging.getLogger("hermes.platform.evolution.bot_curator")


@dataclass
class CuratorState:
    profile_id: str
    last_processed_seq: int = 0
    last_run_timestamp: float = 0.0
    experiences_processed_total: int = 0
    proposals_generated_total: int = 0
    lease_owner: Optional[str] = None
    lease_expires_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CuratorState:
        return cls(
            profile_id=data.get("profile_id", "default"),
            last_processed_seq=int(data.get("last_processed_seq", 0)),
            last_run_timestamp=float(data.get("last_run_timestamp", 0.0)),
            experiences_processed_total=int(data.get("experiences_processed_total", 0)),
            proposals_generated_total=int(data.get("proposals_generated_total", 0)),
            lease_owner=data.get("lease_owner"),
            lease_expires_at=float(data.get("lease_expires_at", 0.0)),
        )


class EvolutionCurator:
    """Autonomous curator for bot experiences and identity evolution proposals."""

    def __init__(
        self,
        event_store: EventStore,
        id_mgr: IdentityManager,
        evo_mgr: BotEvolutionManager,
        council_mgr: Optional[CouncilManager] = None,
        state_dir: Optional[Path] = None,
        profile_id: str = "default",
        curator_id: Optional[str] = None,
    ):
        self.store = event_store
        self.id_mgr = id_mgr
        self.evo_mgr = evo_mgr
        self.council_mgr = council_mgr
        self.profile_id = profile_id
        self.curator_id = curator_id or f"curator-{os.getpid()}-{int(time.time())}"
        
        if state_dir:
            self.state_dir = Path(state_dir)
        else:
            try:
                from hermes_constants import get_hermes_home
                self.state_dir = Path(get_hermes_home()) / "evolution"
            except ImportError:
                self.state_dir = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "evolution"

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / f"curator_state_{self.profile_id}.json"
        self._state = self._load_state()

    def _load_state(self) -> CuratorState:
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return CuratorState.from_dict(json.load(f))
            except Exception as e:
                logger.warning(f"Failed to read curator state from {self.state_file}: {e}")
        return CuratorState(profile_id=self.profile_id)

    def _save_state(self) -> None:
        tmp_file = self.state_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(self._state.to_dict(), f, indent=2)
            tmp_file.replace(self.state_file)
        except Exception as e:
            logger.error(f"Failed to save curator state to {self.state_file}: {e}")

    def acquire_lease(self, ttl_seconds: float = 60.0) -> bool:
        """Acquire a single-flight lease to prevent concurrent background runs in the same profile."""
        now = time.time()
        # Reload state to check current lease
        self._state = self._load_state()

        if self._state.lease_owner and self._state.lease_expires_at > now:
            if self._state.lease_owner != self.curator_id:
                logger.info(f"Curator lease held by '{self._state.lease_owner}' until {self._state.lease_expires_at}")
                return False

        # Acquire or renew
        self._state.lease_owner = self.curator_id
        self._state.lease_expires_at = now + ttl_seconds
        self._save_state()
        return True

    def release_lease(self) -> None:
        """Release the single-flight lease."""
        self._state = self._load_state()
        if self._state.lease_owner == self.curator_id:
            self._state.lease_owner = None
            self._state.lease_expires_at = 0.0
            self._save_state()

    def get_status(self) -> Dict[str, Any]:
        """Return the current health and progress status of the curator."""
        self._state = self._load_state()
        return {
            "profile_id": self.profile_id,
            "curator_id": self.curator_id,
            "last_processed_seq": self._state.last_processed_seq,
            "last_run_timestamp": self._state.last_run_timestamp,
            "experiences_processed_total": self._state.experiences_processed_total,
            "proposals_generated_total": self._state.proposals_generated_total,
            "lease_active": bool(self._state.lease_owner and self._state.lease_expires_at > time.time()),
            "lease_owner": self._state.lease_owner,
        }

    def collect_unprocessed_experiences(self) -> tuple[List[ExperienceEvent], int]:
        """Fetch all experiences recorded since last cursor."""
        events = self.store.get_all()
        new_experiences: List[ExperienceEvent] = []
        max_seq = self._state.last_processed_seq

        for idx, ev in enumerate(events):
            # Use 1-based index as monotonic sequence if seq attribute is missing
            seq = getattr(ev, "seq", idx + 1)
            if seq <= self._state.last_processed_seq:
                continue

            if ev.name == EVENT_EXPERIENCE_RECORDED:
                payload = ev.payload or {}
                exp_data = payload.get("experience")
                if exp_data:
                    try:
                        exp = ExperienceEvent.from_dict(exp_data)
                        new_experiences.append(exp)
                    except Exception as e:
                        logger.warning(f"Corrupt ExperienceEvent payload in event {ev.event_id}: {e}")

            if seq > max_seq:
                max_seq = seq

        return new_experiences, max_seq

    def synthesize_proposals(
        self,
        experiences: List[ExperienceEvent],
        failure_threshold: int = 2,
        success_threshold: int = 4,
    ) -> List[Dict[str, Any]]:
        """Analyze experience patterns per (bot_id, domain) and formulate proposals."""
        grouped: Dict[tuple[str, str], List[ExperienceEvent]] = {}
        for exp in experiences:
            key = (exp.bot_id, exp.domain)
            grouped.setdefault(key, []).append(exp)

        proposals_to_create = []

        for (bot_id, domain), items in grouped.items():
            failures = [it for it in items if not it.success]
            successes = [it for it in items if it.success]

            # Resolve active bot version to pin base_version_hash
            active_ver = self.id_mgr.get_active_version(bot_id)
            if not active_ver:
                logger.debug(f"Bot '{bot_id}' has no active version; skipping proposal formulation.")
                continue

            base_hash = active_ver.bundle_hash

            # Pattern A: Recurring domain failures -> Synthesize corrective patch
            if len(failures) >= failure_threshold:
                failure_summaries = "; ".join(f.summary for f in failures)
                rationale = f"Corrective adaptation in domain '{domain}' after {len(failures)} failures: {failure_summaries}"
                patch = f"## Domain Adaptation: {domain}\n- Enhanced verification and fallback procedures based on observed incidents.\n- Addressed: {failure_summaries}"
                
                proposals_to_create.append({
                    "bot_id": bot_id,
                    "base_version_hash": base_hash,
                    "risk_class": RISK_MEDIUM,
                    "rationale": rationale,
                    "proposed_soul_patch": patch,
                    "evidence_refs": [f.id for f in failures],
                })

            # Pattern B: Sustained excellence -> Reinforce specialization in values
            elif len(successes) >= success_threshold:
                success_summaries = "; ".join(s.summary for s in successes)
                rationale = f"Excellence reinforcement in domain '{domain}' across {len(successes)} validated executions."
                patch = f"## Core Competency: {domain}\n- Proven track record of consistent success."

                proposals_to_create.append({
                    "bot_id": bot_id,
                    "base_version_hash": base_hash,
                    "risk_class": RISK_LOW,
                    "rationale": rationale,
                    "proposed_values_patch": patch,
                    "evidence_refs": [s.id for s in successes],
                })

        return proposals_to_create

    def evaluate_canaries(self) -> Dict[str, Any]:
        """Check proposals currently in canary status against recent performance."""
        canary_proposals = [p for p in self.evo_mgr.get_proposals() if p.status == STATUS_CANARY]
        promoted = []
        rolled_back = []

        if not canary_proposals:
            return {"promoted": promoted, "rolled_back": rolled_back}

        all_events = self.store.get_all()
        # Find experiences occurred after canary proposal creation
        for prop in canary_proposals:
            post_canary_exps = []
            for ev in all_events:
                if ev.name == EVENT_EXPERIENCE_RECORDED:
                    exp_dict = (ev.payload or {}).get("experience", {})
                    if exp_dict.get("bot_id") == prop.bot_id and float(exp_dict.get("occurred_at", 0)) >= prop.updated_at:
                        post_canary_exps.append(exp_dict)

            if len(post_canary_exps) >= 3:
                failures = [e for e in post_canary_exps if not e.get("success")]
                active_ver = self.id_mgr.get_active_version(prop.bot_id)
                curr_hash = active_ver.bundle_hash if active_ver else ""

                if len(failures) == 0:
                    # Canary succeeded with 100% success rate -> Promote!
                    try:
                        self.evo_mgr.update_status(prop.id, STATUS_PROMOTED, curr_hash)
                        promoted.append(prop.id)
                        logger.info(f"Canary proposal {prop.id} promoted successfully for {prop.bot_id}")
                    except Exception as e:
                        logger.warning(f"Failed to promote canary proposal {prop.id}: {e}")
                elif len(failures) >= 2:
                    # Canary failed -> Rollback!
                    try:
                        self.evo_mgr.update_status(prop.id, STATUS_ROLLED_BACK, curr_hash)
                        rolled_back.append(prop.id)
                        logger.info(f"Canary proposal {prop.id} rolled back for {prop.bot_id} due to {len(failures)} failures")
                    except Exception as e:
                        logger.warning(f"Failed to rollback canary proposal {prop.id}: {e}")

        return {"promoted": promoted, "rolled_back": rolled_back}

    def run_cycle(self, dry_run: bool = False) -> Dict[str, Any]:
        """Execute one complete curator cycle with single-flight locking."""
        start_t = time.perf_counter()

        if not self.acquire_lease(ttl_seconds=60.0):
            return {
                "status": "skipped",
                "reason": "lease_locked",
                "profile_id": self.profile_id,
            }

        try:
            # 1. Ingest experiences
            new_experiences, max_seq = self.collect_unprocessed_experiences()
            
            # 2. Formulate proposals
            candidate_proposals = self.synthesize_proposals(new_experiences)
            created_proposals = []

            if not dry_run:
                for cand in candidate_proposals:
                    p = self.evo_mgr.create_proposal(
                        bot_id=cand["bot_id"],
                        base_version_hash=cand["base_version_hash"],
                        risk_class=cand["risk_class"],
                        rationale=cand["rationale"],
                        proposed_soul_patch=cand.get("proposed_soul_patch"),
                        proposed_values_patch=cand.get("proposed_values_patch"),
                        evidence_refs=cand.get("evidence_refs"),
                    )
                    created_proposals.append(p.id)

                # 3. Evaluate active canaries
                canary_summary = self.evaluate_canaries()

                # 4. Advance durable state cursor
                self._state.last_processed_seq = max_seq
                self._state.last_run_timestamp = time.time()
                self._state.experiences_processed_total += len(new_experiences)
                self._state.proposals_generated_total += len(created_proposals)
                self._save_state()
            else:
                canary_summary = {"promoted": [], "rolled_back": []}

            duration_ms = (time.perf_counter() - start_t) * 1000.0

            return {
                "status": "completed",
                "profile_id": self.profile_id,
                "dry_run": dry_run,
                "experiences_ingested": len(new_experiences),
                "proposals_generated": len(created_proposals) if not dry_run else len(candidate_proposals),
                "created_proposal_ids": created_proposals,
                "canary_evaluation": canary_summary,
                "cursor_seq": self._state.last_processed_seq,
                "duration_ms": duration_ms,
            }

        finally:
            self.release_lease()


__all__ = ["EvolutionCurator", "CuratorState"]
