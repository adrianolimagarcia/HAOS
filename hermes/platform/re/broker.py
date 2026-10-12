"""Permission Broker and Audit Decision Log for Reverse Engineering Tools (Phase 5).

Dual-use security gate enforcing:
- Exact grants only (no generic 're.*' wildcards).
- Target hashing (SHA-256 of inspected binaries).
- Canonical allowlist verification (/bin, /usr/bin, repo tests).
- Asymmetric timeout policies (abort on mutation/write timeouts).
- Structured audit decision logging (allow, deny, timeout).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Union

from hermes_constants import get_hermes_home

logger = logging.getLogger("hermes.platform.re.broker")

# Exact allowed grants registry - wildcards prohibited
VALID_RE_GRANTS: Set[str] = {
    "triage.inspect",
    "r2.read_functions",
    "r2.disassemble",
    "r2.decompile",
    "r2.write_patch",
    "ghidra.decompile",
    "ghidra.modify_program",
}

MUTATING_GRANTS: Set[str] = {
    "r2.write",
    "r2.write_patch",
    "ghidra.modify_program",
}


@dataclass
class DecisionLogEntry:
    timestamp: float
    requester: str
    grant: str
    target_path: str
    target_sha256: str
    outcome: str  # "allow", "deny", "timeout"
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def __contains__(self, item: str) -> bool:
        return hasattr(self, item)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class REPermissionBroker:
    """Brokers access to dual-use Reverse Engineering tools and logs decisions."""

    def __init__(
        self,
        repo_root: Optional[Path] = None,
        audit_log_path: Optional[Path] = None,
        operator_approval_hook: Optional[Callable[[str, str, str], bool]] = None,
        decision_log_path: Optional[Path] = None,
        allowlisted_dirs: Optional[Iterable[Union[str, Path]]] = None,
    ) -> None:
        self.repo_root = (repo_root or Path.cwd()).resolve()
        log_path = decision_log_path or audit_log_path
        self.audit_log_path = log_path or (Path(get_hermes_home()) / "decision-logs" / "re_audit.jsonl")
        self.operator_approval_hook = operator_approval_hook
        self.decision_log: List[DecisionLogEntry] = []

        if allowlisted_dirs is not None:
            self.allowlist_dirs = [Path(p).resolve() for p in allowlisted_dirs]
        else:
            # Allowlist directories: /bin, /usr/bin, and repo test directories
            self.allowlist_dirs = [
                Path("/bin").resolve(),
                Path("/usr/bin").resolve(),
                (self.repo_root / "tests").resolve(),
            ]

    def get_decision_log(self) -> List[DecisionLogEntry]:
        """Returns the list of decision log entries in memory."""
        return list(self.decision_log)

    def get_decisions(self) -> List[DecisionLogEntry]:
        """Alias for get_decision_log()."""
        return self.get_decision_log()

    def _compute_sha256(self, target_path: Path) -> str:
        if not target_path.is_file():
            return "NON_EXISTENT"
        hasher = hashlib.sha256()
        try:
            with target_path.open("rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception as exc:
            return f"HASH_ERROR_{exc}"

    def _is_path_allowlisted(self, target_path: Path) -> bool:
        resolved = target_path.resolve()
        for allowed in self.allowlist_dirs:
            if resolved == allowed or allowed in resolved.parents:
                return True
        return False

    def _record_decision(
        self,
        requester: str,
        grant: str,
        target_path: Path,
        sha256: str,
        outcome: str,
        reason: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> DecisionLogEntry:
        entry = DecisionLogEntry(
            timestamp=time.time(),
            requester=requester,
            grant=grant,
            target_path=str(target_path.resolve()),
            target_sha256=sha256,
            outcome=outcome,
            reason=reason,
            details=details or {},
        )
        self.decision_log.append(entry)

        # Write to durable audit log if directory accessible
        try:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log_path.open("a", encoding="utf-8") as f:
                f.write(entry.to_json() + "\n")
        except Exception as exc:
            logger.warning("Could not persist RE audit decision log: %s", exc)

        return entry

    def evaluate_request(
        self,
        requester: str,
        grant: str,
        target: str | Path,
        operator_approved: bool = False,
    ) -> tuple[bool, str]:
        """Evaluates a requested RE tool operation.

        Returns (allowed: bool, reason: str).
        """
        # 1. Enforce exact grant validation (no wildcards allowed)
        if grant not in VALID_RE_GRANTS:
            target_path = Path(target)
            sha256 = self._compute_sha256(target_path)
            reason = f"Grant '{grant}' is invalid or contains prohibited wildcards."
            self._record_decision(requester, grant, target_path, sha256, "deny", reason)
            return False, reason

        target_path = Path(target).resolve()
        sha256 = self._compute_sha256(target_path)

        # 2. Check allowlist
        if self._is_path_allowlisted(target_path):
            reason = f"Target {target_path} is within system/test allowlist."
            self._record_decision(requester, grant, target_path, sha256, "allow", reason)
            return True, reason

        # 3. Target outside allowlist requires explicit operator approval
        if operator_approved:
            reason = f"Target {target_path} outside allowlist but explicit operator approval was provided."
            self._record_decision(requester, grant, target_path, sha256, "allow", reason)
            return True, reason

        if self.operator_approval_hook:
            approved = self.operator_approval_hook(requester, grant, str(target_path))
            if approved:
                reason = f"Target {target_path} approved via dynamic operator hook."
                self._record_decision(requester, grant, target_path, sha256, "allow", reason)
                return True, reason

        reason = f"Target {target_path} is outside allowlist and lacks operator approval."
        self._record_decision(requester, grant, target_path, sha256, "deny", reason)
        return False, reason

    def handle_timeout(
        self,
        requester: str,
        grant: str,
        target: str | Path,
        elapsed_seconds: float,
    ) -> str:
        """Handles execution timeout with asymmetric policy:

        Mutating operations abort immediately (fail-closed).
        Read operations record timeout outcome cleanly.
        """
        target_path = Path(target).resolve()
        sha256 = self._compute_sha256(target_path)
        is_mutating = grant in MUTATING_GRANTS

        action = "abort" if is_mutating else "timeout"
        reason = (
            f"Execution timed out after {elapsed_seconds}s on mutating operation -> ABORT fail-closed."
            if is_mutating
            else f"Execution timed out after {elapsed_seconds}s on read operation."
        )

        self._record_decision(
            requester,
            grant,
            target_path,
            sha256,
            action,
            reason,
            {"elapsed_seconds": elapsed_seconds, "is_mutating": is_mutating},
        )
        return action
