"""Kanban Rust Candidate Selector Bridge (Read-Only Opt-in).

Integrates the fast read-only candidate selector from haos-edge into the Python
Kanban dispatcher while strictly preserving Python's ownership of:
- single-writer locks
- transaction boundaries
- profile isolation & gating
- claim locks & TTL leases
- workspaces & worktrees
- process spawning and failure reclamation
"""

import json
import logging
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("hermes.kanban.rust_selector")

CONTRACT_VERSION = "kanban_selector_v1"
DEFAULT_TIMEOUT_SECONDS = 2.0


@dataclass
class SelectorTaskCandidate:
    id: str
    assignee: Optional[str]
    priority: int
    created_at: int
    status: str

    def to_row_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "assignee": self.assignee,
            "priority": self.priority,
            "created_at": self.created_at,
            "status": self.status,
        }


def get_rust_selector_mode(kanban_cfg: Optional[dict] = None) -> str:
    """Return selector mode: 'off' (default), 'shadow', or 'rust'.

    Can also be controlled via HERMES_KANBAN_RUST_SELECTOR env var:
    - 'off' / '0' / 'false'
    - 'shadow'
    - 'rust' / '1' / 'true'
    """
    env_mode = os.environ.get("HERMES_KANBAN_RUST_SELECTOR", "").strip().lower()
    if env_mode in ("rust", "1", "true"):
        return "rust"
    if env_mode == "shadow":
        return "shadow"
    if env_mode in ("off", "0", "false"):
        return "off"

    if kanban_cfg is None:
        try:
            from hermes_cli.config import load_config_readonly
            kanban_cfg = (load_config_readonly() or {}).get("kanban", {})
        except Exception:
            kanban_cfg = {}
    kanban_cfg = kanban_cfg or {}
    raw_mode = str(kanban_cfg.get("rust_selector", "off")).strip().lower()
    if raw_mode in ("rust", "1", "true"):
        return "rust"
    if raw_mode == "shadow":
        return "shadow"
    return "off"


def resolve_haos_edge_binary(kanban_cfg: Optional[dict] = None) -> Optional[Path]:
    """Locate the haos-edge binary via config, env override, canonical PATH, or system install.

    Never auto-probes debug target folders unless explicitly configured.
    """
    if kanban_cfg is None:
        try:
            from hermes_cli.config import load_config_readonly
            kanban_cfg = (load_config_readonly() or {}).get("kanban", {})
        except Exception:
            kanban_cfg = {}
    kanban_cfg = kanban_cfg or {}

    # 1. Explicit config path wins
    cfg_bin = str(kanban_cfg.get("rust_selector_binary", "")).strip()
    if cfg_bin:
        p = Path(cfg_bin).expanduser().resolve()
        if p.is_file() and os.access(p, os.X_OK):
            return p

    # 2. Explicit environment override
    override = os.environ.get("HAOS_EDGE_BIN", "").strip()
    if override:
        p = Path(override).expanduser().resolve()
        if p.is_file() and os.access(p, os.X_OK):
            return p

    # 3. Canonical system and distro locations
    canonical_candidates = [
        Path("/usr/local/bin/haos-edge"),
        Path("/opt/haos/bin/haos-edge"),
        Path("/usr/bin/haos-edge"),
    ]
    for cand in canonical_candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand

    which_edge = shutil.which("haos-edge")
    if which_edge:
        return Path(which_edge).resolve()

    return None


def query_rust_candidates(
    db_path: Path,
    *,
    profile: str,
    data_dir: Path,
    include_review: bool = False,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    binary_path: Optional[Path] = None,
) -> Optional[list[dict[str, Any]]]:
    """Query candidates using haos-edge kanban-select.

    Returns a list of dicts [{'id': ..., 'assignee': ..., 'status': ...}, ...] or None if failed.
    Fail-open design: does NOT raise, returns None on any failure so caller falls back to Python.
    """
    edge_bin = binary_path or resolve_haos_edge_binary()
    if not edge_bin:
        logger.debug("haos-edge binary not found; skipping Rust selector")
        return None

    cmd = [
        str(edge_bin),
        "kanban-select",
        "--db-path",
        str(db_path.resolve()),
        "--profile",
        profile,
        "--data-dir",
        str(data_dir.resolve()),
    ]
    if include_review:
        cmd.append("--include-review")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("haos-edge kanban-select timed out after %s seconds", timeout_seconds)
        return None
    except Exception as exc:
        logger.warning("haos-edge invocation failed: %s", exc)
        return None

    if proc.returncode != 0:
        logger.warning(
            "haos-edge kanban-select exited with code %s: %s",
            proc.returncode,
            proc.stderr.strip() or proc.stdout.strip(),
        )
        return None

    try:
        data = json.loads(proc.stdout)
        if not isinstance(data, dict):
            logger.warning("Invalid JSON payload from haos-edge: expected dict")
            return None
        if data.get("contract_version") != CONTRACT_VERSION:
            logger.warning(
                "Mismatched contract version from haos-edge: got %s, expected %s",
                data.get("contract_version"),
                CONTRACT_VERSION,
            )
            return None
        if not data.get("ok"):
            logger.warning("haos-edge reported error: %s", data.get("error"))
            return None
        candidates = data.get("candidates")
        if not isinstance(candidates, list):
            logger.warning("haos-edge returned non-list candidates field")
            return None

        # Validate candidate schema, bounds, and deduplicate IDs
        seen_ids: set[str] = set()
        validated: list[dict[str, Any]] = []
        for idx, item in enumerate(candidates):
            if not isinstance(item, dict):
                logger.warning("Candidate at index %s is not a dict: %r", idx, item)
                return None
            cid = item.get("id")
            if not isinstance(cid, str) or not cid.strip():
                logger.warning("Candidate at index %s has invalid/missing id: %r", idx, item)
                return None
            if cid in seen_ids:
                logger.warning("Candidate at index %s has duplicate id %s", idx, cid)
                return None
            seen_ids.add(cid)

            # Normalise status / lane
            lane = item.get("lane") or item.get("status")
            if lane not in ("ready", "review"):
                logger.warning("Candidate %s has invalid lane/status %r", cid, lane)
                return None

            priority = item.get("priority", 0)
            if not isinstance(priority, int):
                logger.warning("Candidate %s has non-integer priority %r", cid, priority)
                return None

            created_at = item.get("created_at", 0)
            if not isinstance(created_at, int):
                logger.warning("Candidate %s has non-integer created_at %r", cid, created_at)
                return None

            assignee = item.get("assignee")
            if assignee is not None and not isinstance(assignee, str):
                logger.warning("Candidate %s has invalid assignee %r", cid, assignee)
                return None

            validated.append({
                "id": cid,
                "assignee": assignee,
                "status": lane,
                "lane": lane,
                "priority": priority,
                "created_at": created_at,
            })

        return validated
    except Exception as exc:
        logger.warning("Failed to decode haos-edge JSON response: %s", exc)
        return None
