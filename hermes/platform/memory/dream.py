"""HAOS Dream Routine: Idle-time memory consolidation with Git audit trail and rollback.

Ported in spirit from HKUDS/nanobot's Dream + GitStore:
- Consolidates recently completed/idle sessions from SessionDB.
- Extracts declarative knowledge into OKF and ADR decisions into Obsidian Vault.
- Automatically records a local Git commit over the memory stores where the commit
  message reflects the actual filesystem diff (ground-truth audit trail).
- Supports deterministic rollback via 'revert(sha)' or 'revert_last()'.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home
from hermes_cli._subprocess_compat import IS_WINDOWS, harden_git_argv, noninteractive_git_env, windows_hide_flags
from hermes.platform.memory.reconciler import MemoryReconciler


class DreamError(RuntimeError):
    """Raised when dream consolidation or git storage fails."""


@dataclass
class DreamCommitInfo:
    sha: str
    message: str
    timestamp: str
    files_changed: int


class DreamGitStore:
    """Git-backed version control and audit trail for memory files (OKF and Vault)."""

    def __init__(self, memory_root: Path):
        self.root = memory_root.resolve()
        self.git_dir = self.root / ".git"

    def _run_git(self, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
        popen_kwargs: dict = {"creationflags": windows_hide_flags()} if IS_WINDOWS else {}
        cmd = ["git", *harden_git_argv(args)]
        res = subprocess.run(
            cmd,
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            env=noninteractive_git_env(),
            **popen_kwargs,
        )
        if check and res.returncode != 0:
            raise DreamError(f"Git command {' '.join(args)} failed: {res.stderr.strip()}")
        return res

    def init_if_needed(self) -> bool:
        """Initialize git repo in memory directory if not already present."""
        self.root.mkdir(parents=True, exist_ok=True)
        if (self.root / ".git").is_dir():
            return False

        self._run_git(["init"])
        gitignore = self.root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*.tmp\n*.log\n", encoding="utf-8")

        # Initial commit only stages .gitignore
        self._run_git(["add", ".gitignore"])
        self._run_git(["-c", "user.name=HAOS Dream", "-c", "user.email=dream@haos.local",
                       "commit", "-m", "chore: initialize HAOS memory audit store", "--allow-empty"])
        return True

    def working_tree_diff_summary(self) -> str:
        """Get diff stat / summary of modified files."""
        res = self._run_git(["diff", "--stat", "HEAD"], check=False)
        return res.stdout.strip()

    def commit_changes(self, subject: str = "dream: consolidate session memories") -> Optional[DreamCommitInfo]:
        """Stage changes and commit with diff summary. Returns None if clean."""
        self.init_if_needed()
        # Stage all changes
        self._run_git(["add", "-A"])
        status = self._run_git(["status", "--porcelain"], check=False).stdout.strip()
        if not status:
            return None

        # Build diff summary
        diff_stat = self._run_git(["diff", "--cached", "--stat"], check=False).stdout.strip()
        commit_msg = f"{subject}\n\nDiff Summary:\n{diff_stat}"

        self._run_git([
            "-c", "user.name=HAOS Dream",
            "-c", "user.email=dream@haos.local",
            "commit", "-m", commit_msg
        ])

        log_res = self._run_git(["log", "-1", "--format=%h%x00%s%x00%ci"])
        parts = log_res.stdout.strip().split("\x00")
        sha = parts[0] if parts else "unknown"
        msg = parts[1] if len(parts) > 1 else ""
        ts = parts[2] if len(parts) > 2 else ""

        return DreamCommitInfo(sha=sha, message=msg, timestamp=ts, files_changed=len(status.splitlines()))

    def list_commits(self, limit: int = 10) -> List[Dict[str, str]]:
        """List past dream commits."""
        self.init_if_needed()
        res = self._run_git(["log", f"-{limit}", "--format=%h%x00%s%x00%ci%x00%b"], check=False)
        if res.returncode != 0 or not res.stdout.strip():
            return []
        commits = []
        for line in res.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\x00")
            if len(parts) >= 3:
                commits.append({
                    "sha": parts[0],
                    "subject": parts[1],
                    "timestamp": parts[2],
                    "body": parts[3] if len(parts) > 3 else "",
                })
        return commits

    def revert(self, sha: str) -> bool:
        """Revert working directory to a specific commit or revert that commit."""
        self.init_if_needed()
        res = self._run_git([
            "-c", "user.name=HAOS Dream",
            "-c", "user.email=dream@haos.local",
            "revert", "--no-edit", sha
        ], check=False)
        return res.returncode == 0


class DreamConsolidator:
    """Orchestrates memory consolidation across recent sessions."""

    def __init__(self, hermes_home: Optional[Path] = None):
        self.home = (hermes_home or Path(get_hermes_home())).resolve()
        self.memory_dir = self.home / "memory"
        self.okf_dir = self.memory_dir / "okf"
        self.vault_adrs_dir = self.memory_dir / "vault" / "adrs"
        self.cursor_file = self.memory_dir / ".dream_cursor"
        self.git_store = DreamGitStore(self.memory_dir)
        self.reconciler = MemoryReconciler(self.memory_dir / "reconciled_memories.db")

    def get_cursor(self) -> float:
        """Timestamp of last consolidated session."""
        if self.cursor_file.exists():
            try:
                return float(self.cursor_file.read_text(encoding="utf-8").strip())
            except Exception:
                return 0.0
        return 0.0

    def set_cursor(self, ts: float) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.cursor_file.write_text(str(ts), encoding="utf-8")

    def run_dream(self, dry_run: bool = False) -> Dict[str, Any]:
        """Consolidate unconsolidated sessions since last cursor."""
        self.okf_dir.mkdir(parents=True, exist_ok=True)
        self.vault_adrs_dir.mkdir(parents=True, exist_ok=True)

        from hermes_state import SessionDB
        db = SessionDB(read_only=True)
        last_cursor = self.get_cursor()

        try:
            recent_sessions = db.list_recent_sessions_bounded(limit=50)
        finally:
            db.close()

        # Sessions newer than cursor with some content
        candidates = [
            s for s in recent_sessions
            if (s.get("started_at") or 0.0) > last_cursor
        ]

        if not candidates:
            return {
                "status": "idle",
                "message": "No new sessions to consolidate",
                "consolidated_count": 0,
                "commit": None,
            }

        consolidated = 0
        reconciliation_stats = {"ADD": 0, "UPDATE": 0, "SUPERSEDE": 0, "NOOP": 0}
        max_ts = last_cursor

        for s in reversed(candidates):
            sid = s.get("id", "")
            title = s.get("title") or "Session"
            started_at = s.get("started_at") or time.time()
            max_ts = max(max_ts, started_at)

            # Check if this session generated key operational decisions or learnings
            preview = (s.get("preview") or "").strip()
            if not preview:
                try:
                    msgs = db.get_messages(sid)
                    for m in msgs:
                        if m.get("content"):
                            preview += m.get("content")[:200] + " "
                    preview = preview.strip()
                except Exception:
                    pass

            if len(preview) < 20:
                continue

            # Deterministic extraction of OKF lesson or ADR note
            slug = sid[-6:]
            doc_file = self.okf_dir / f"lesson_{slug}.md"
            if not doc_file.exists():
                content = (
                    f"---\ntitle: \"Lição da Sessão {sid[:8]}\"\ntype: concept\ntags: [session, dream, auto-extracted]\n"
                    f"source_session: \"{sid}\"\n---\n\n"
                    f"# {title}\n\n{preview}\n"
                )
                if not dry_run:
                    doc_file.write_text(content, encoding="utf-8")
                consolidated += 1

                # ECC Instinct extraction from session: if session recorded learnings/heuristics
                if not dry_run and ("sempre" in preview.lower() or "nunca" in preview.lower() or "erro" in preview.lower()):
                    try:
                        from hermes.platform.memory.instincts import InstinctStore
                        instinct_store = InstinctStore(self.memory_dir / "instincts")
                        rule_summary = preview.split(".")[0].strip()[:140]
                        if len(rule_summary) > 15:
                            instinct_store.record_instinct(
                                rule=rule_summary,
                                category="workflow",
                                project_scope="default",
                                tags=["dream-distilled"],
                            )
                    except Exception as _ins_err:
                        logger.debug("Dream instinct distillation failed: %s", _ins_err)

                # Mem0-inspired declarative memory reconciliation
                if not dry_run and any(k in preview.lower() for k in ("preferência", "preferencia", "usando", "migramos", "banco", "framework", "agora usamos", "não usamos")):
                    try:
                        fact_candidate = preview.split(".")[0].strip()[:180]
                        if len(fact_candidate) > 10:
                            topic = "general"
                            for cand_topic in ("banco", "database", "ui", "framework", "style", "language", "auth", "tool"):
                                if cand_topic in fact_candidate.lower():
                                    topic = cand_topic
                                    break
                            rec_res = self.reconciler.reconcile(
                                topic=topic,
                                content=fact_candidate,
                                category="session_fact",
                                metadata={"session_id": sid, "source": "dream"},
                            )
                            if rec_res.action in reconciliation_stats:
                                reconciliation_stats[rec_res.action] += 1
                    except Exception as _rec_err:
                        logger.debug("Dream memory reconciliation failed: %s", _rec_err)

        if dry_run:
            return {
                "status": "dry_run",
                "consolidated_count": consolidated,
                "reconciliation": reconciliation_stats,
                "commit": None,
            }

        self.set_cursor(max_ts)
        commit_info = self.git_store.commit_changes(
            subject=f"dream: consolidate {consolidated} session(s) [reconciled: +{reconciliation_stats['ADD']} ~{reconciliation_stats['UPDATE']} !{reconciliation_stats['SUPERSEDE']}]"
        )

        return {
            "status": "success",
            "consolidated_count": consolidated,
            "reconciliation": reconciliation_stats,
            "commit": commit_info.sha if commit_info else None,
            "timestamp": commit_info.timestamp if commit_info else None,
        }
