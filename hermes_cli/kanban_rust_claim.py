"""Kanban Rust Claim & Lease Fencing Bridge.

Integrates the fast atomic claim and heartbeat operations from haos-edge into
the Python Kanban dispatcher and task lifecycle while preserving Python's
hooks, Task representations, and graceful fallbacks.
"""

import json
import logging
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Optional

from hermes_cli.kanban_db import (
    Task,
    _claimer_id,
    _fire_task_hook,
    _resolve_claim_ttl_seconds,
    claim_review_task,
    claim_task,
    get_task,
    heartbeat_claim,
)

logger = logging.getLogger("hermes.kanban.rust_claim")

CONTRACT_VERSION = "kanban_claim_v1"
DEFAULT_TIMEOUT_SECONDS = 5.0


def get_rust_claim_mode(kanban_cfg: Optional[dict] = None) -> str:
    """Return claim mode: 'off' (default) or 'rust'.

    Can also be controlled via HERMES_KANBAN_RUST_CLAIM env var:
    - 'off' / '0' / 'false'
    - 'rust' / '1' / 'true'
    """
    env_mode = os.environ.get("HERMES_KANBAN_RUST_CLAIM", "").strip().lower()
    if env_mode in ("rust", "1", "true"):
        return "rust"
    if env_mode in ("off", "0", "false"):
        return "off"

    if kanban_cfg is None:
        try:
            from hermes_cli.config import load_config_readonly
            kanban_cfg = (load_config_readonly() or {}).get("kanban", {})
        except Exception:
            kanban_cfg = {}
    kanban_cfg = kanban_cfg or {}
    raw_mode = str(kanban_cfg.get("rust_claim", "off")).strip().lower()
    if raw_mode in ("rust", "1", "true"):
        return "rust"
    return "off"


def resolve_haos_edge_binary(kanban_cfg: Optional[dict] = None) -> Optional[Path]:
    """Locate the haos-edge binary via config, env override, canonical PATH, dev/test target, or system install."""
    if kanban_cfg is None:
        try:
            from hermes_cli.config import load_config_readonly
            kanban_cfg = (load_config_readonly() or {}).get("kanban", {})
        except Exception:
            kanban_cfg = {}
    kanban_cfg = kanban_cfg or {}

    # 1. Explicit config path wins
    cfg_bin = str(kanban_cfg.get("rust_claim_binary", "")).strip()
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

    # 4. Dev / test target paths (e.g. ./packages/haos-edge/target/debug/haos-edge or repo target)
    repo_root = Path(__file__).resolve().parents[1]
    dev_candidates = [
        repo_root / "packages" / "haos-edge" / "target" / "debug" / "haos-edge",
        repo_root / "target" / "debug" / "haos-edge",
    ]
    for cand in dev_candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand.resolve()

    return None


def _get_db_path_from_conn(conn: sqlite3.Connection) -> Optional[Path]:
    """Extract SQLite database file path from an open connection."""
    try:
        cur = conn.execute("PRAGMA database_list")
        for row in cur.fetchall():
            # row: (seq, name, file)
            name = row[1] if isinstance(row, (list, tuple)) else row["name"]
            file_path = row[2] if isinstance(row, (list, tuple)) else row["file"]
            if name == "main" and file_path:
                return Path(file_path).resolve()
    except Exception as exc:
        logger.debug("Failed to extract db_path from connection: %s", exc)
    return None


def rust_claim_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    profile: str,
    data_dir: Path,
    claimer: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    lane: str = "ready",
    binary_path: Optional[Path] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Optional[Task]:
    """Atomically claim a task using Rust haos-edge kanban-claim.

    Falls back smoothly to Python implementation on any error or missing binary.
    """
    resolved_ttl = _resolve_claim_ttl_seconds(ttl_seconds)
    resolved_claimer = claimer or _claimer_id()
    edge_bin = binary_path or resolve_haos_edge_binary()
    db_path = _get_db_path_from_conn(conn)

    # If edge binary or db path cannot be determined, fallback to Python
    if not edge_bin or not db_path:
        logger.warning(
            "Rust claim unavailable (edge_bin=%s, db_path=%s); falling back to Python",
            edge_bin,
            db_path,
        )
        if lane == "review":
            return claim_review_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)
        return claim_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    cmd = [
        str(edge_bin),
        "kanban-claim",
        "--db-path",
        str(db_path.resolve()),
        "--profile",
        profile,
        "--data-dir",
        str(data_dir.resolve()),
        "--task-id",
        task_id,
        "--claimer",
        resolved_claimer,
        "--ttl",
        str(resolved_ttl),
        "--source-status",
        lane,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("haos-edge kanban-claim timed out after %s seconds; falling back to Python", timeout_seconds)
        if lane == "review":
            return claim_review_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)
        return claim_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)
    except Exception as exc:
        logger.warning("haos-edge kanban-claim subprocess failed: %s; falling back to Python", exc)
        if lane == "review":
            return claim_review_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)
        return claim_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    # Scope/binding errors are authoritative refusals: falling back would permit
    # Python to mutate a task even though the Rust profile check rejected it.
    if proc.returncode in (2, 3):
        logger.warning(
            "haos-edge kanban-claim rejected profile/database scope (exit %s): %s",
            proc.returncode,
            proc.stderr.strip() or proc.stdout.strip(),
        )
        return None

    try:
        data = json.loads(proc.stdout)
    except Exception as exc:
        logger.warning(
            "Failed to parse JSON from haos-edge kanban-claim (code %s): %s; output: %r; stderr: %r. Falling back to Python",
            proc.returncode,
            exc,
            proc.stdout,
            proc.stderr,
        )
        if lane == "review":
            return claim_review_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)
        return claim_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    if not isinstance(data, dict):
        logger.warning("Invalid payload from haos-edge kanban-claim: expected dict. Falling back to Python")
        if lane == "review":
            return claim_review_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)
        return claim_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    if data.get("contract_version") != CONTRACT_VERSION:
        logger.warning(
            "Mismatched contract version from haos-edge: got %r, expected %r. Falling back to Python",
            data.get("contract_version"),
            CONTRACT_VERSION,
        )
        if lane == "review":
            return claim_review_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)
        return claim_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    if not data.get("ok"):
        error_msg = data.get("error", "unknown error")
        logger.warning("haos-edge kanban-claim reported error: %s; falling back to Python", error_msg)
        if lane == "review":
            return claim_review_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)
        return claim_task(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    # Claim executed by Rust
    if data.get("claimed") is not True:
        # Legitimate claim rejection or conflict (e.g. dependencies or CAS).
        return None
    run_id = data.get("run_id")
    claimed_task = get_task(conn, task_id)
    _fire_task_hook("kanban_task_claimed", claimed_task, task_id, run_id)
    return claimed_task


def rust_heartbeat_claim(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    profile: str,
    data_dir: Path,
    claimer: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    binary_path: Optional[Path] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> bool:
    """Extend a running task lease using Rust haos-edge kanban-heartbeat.

    Falls back smoothly to Python heartbeat_claim on any error or missing binary.
    """
    resolved_ttl = _resolve_claim_ttl_seconds(ttl_seconds)
    resolved_claimer = claimer or _claimer_id()
    edge_bin = binary_path or resolve_haos_edge_binary()
    db_path = _get_db_path_from_conn(conn)

    if not edge_bin or not db_path:
        logger.warning(
            "Rust heartbeat unavailable (edge_bin=%s, db_path=%s); falling back to Python",
            edge_bin,
            db_path,
        )
        return heartbeat_claim(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    cmd = [
        str(edge_bin),
        "kanban-heartbeat",
        "--db-path",
        str(db_path.resolve()),
        "--profile",
        profile,
        "--data-dir",
        str(data_dir.resolve()),
        "--task-id",
        task_id,
        "--claimer",
        resolved_claimer,
        "--ttl",
        str(resolved_ttl),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("haos-edge kanban-heartbeat timed out after %s seconds; falling back to Python", timeout_seconds)
        return heartbeat_claim(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)
    except Exception as exc:
        logger.warning("haos-edge kanban-heartbeat subprocess failed: %s; falling back to Python", exc)
        return heartbeat_claim(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    try:
        data = json.loads(proc.stdout)
    except Exception as exc:
        logger.warning(
            "Failed to parse JSON from haos-edge kanban-heartbeat (code %s): %s; output: %r; stderr: %r. Falling back to Python",
            proc.returncode,
            exc,
            proc.stdout,
            proc.stderr,
        )
        return heartbeat_claim(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    if not isinstance(data, dict):
        logger.warning("Invalid payload from haos-edge kanban-heartbeat: expected dict. Falling back to Python")
        return heartbeat_claim(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    if data.get("contract_version") != CONTRACT_VERSION:
        logger.warning(
            "Mismatched contract version from haos-edge: got %r, expected %r. Falling back to Python",
            data.get("contract_version"),
            CONTRACT_VERSION,
        )
        return heartbeat_claim(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    if not data.get("ok"):
        error_msg = data.get("error", "unknown error")
        logger.warning("haos-edge kanban-heartbeat reported error: %s; falling back to Python", error_msg)
        return heartbeat_claim(conn, task_id, ttl_seconds=resolved_ttl, claimer=resolved_claimer)

    return bool(data.get("renewed", False))
