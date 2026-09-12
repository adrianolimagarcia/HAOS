"""HAOS Dream Routine: Idle-time memory consolidation with Git audit trail and rollback.

Ported in spirit from HKUDS/nanobot's Dream + GitStore:
- Consolidates recently completed/idle sessions from SessionDB.
- Extracts declarative knowledge into OKF and ADR decisions into Obsidian Vault.
- Automatically records a local Git commit over the memory stores where the commit
  message reflects the actual filesystem diff (ground-truth audit trail).
- Supports deterministic rollback via 'revert(sha)' or 'revert_last()'.

P11 — gate de staging:
- Cada lição extraída vira um ``MemoryCandidate`` via ``MemoryRouter.route_fact``
  (classificação determinística por regex, nunca por substring solta no preview cru).
- A confiança do candidato vem do ``InstinctStore`` (0.3 na 1ª aparição; +0.2 por
  recorrência ENTRE sessões; limiar de promoção 0.8).
- A lição só chega à árvore canônica (<home>/okf) quando PROMOVIDA, i.e. quando
  ``MemoryRouter.process_candidate`` consolida (confiança >= 0.85 e sem conflito).
- Abaixo disso o candidato fica PERSISTIDO como ``pending`` no
  ``MemoryStagingStore`` (<home>/memory/staging), com proveniência (id da sessão)
  e confiança — nada é descartado nem promovido por impulso.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from hermes_cli._subprocess_compat import IS_WINDOWS, harden_git_argv, noninteractive_git_env, windows_hide_flags
from hermes.platform.context.memory.candidate import MemoryCandidate
from hermes.platform.context.memory.router import MemoryRouter, STAGE_REASON_CONFLICT
from hermes.platform.context.memory.staging import MemoryStagingStore, PROMOTED, candidate_key
from hermes.platform.memory.instincts import InstinctStore
from hermes.platform.memory.reconciler import MemoryReconciler

logger = logging.getLogger(__name__)

# Escopo de projeto dos instintos do dream: o MESMO lido por
# hermes_cli/haos_cmd.py:391 (get_eligible_promotions("default")) e pela tool
# tools/haos_instinct_tool — reforço e promoção conversam no mesmo namespace.
INSTINCT_PROJECT_SCOPE = "default"

# Categoria do instinto derivada do destino determinístico do MemoryRouter
# (tabela, não if/elif em cadeia — ver Code Shape Rules).
_CATEGORY_BY_DESTINATION: Dict[str, str] = {
    "skill": "workflow",
    "core_agent": "workflow",
    "working": "workflow",
    "obsidian": "domain",
    "core_user": "domain",
    "task": "domain",
}


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


class _CanonicalLessonWriter:
    """Destination writer do MemoryConsolidator: materializa a lição promovida no OKF canônico.

    Só é chamado depois que o candidato passou pelo gate de confiança do
    ``MemoryRouter.process_candidate``. Escreve via ``OKFStore`` — o mesmo writer
    canônico usado por tools/haos_memory_tools — e expõe as lições já canônicas
    (get_existing_facts) para a detecção de conflito do ``MemoryConsolidator``.
    """

    def __init__(self, okf_dir: Path, session_id: str, title: str):
        self.okf_dir = Path(okf_dir)
        self.session_id = session_id
        self.session_title = title
        self.last_written: Optional[Path] = None

    def get_existing_facts(self, destination: str) -> List[Dict[str, str]]:
        from hermes.platform.memory.okf import OKFStore

        store = OKFStore(self.okf_dir)
        return [
            {"id": doc.relative_path, "content": doc.body}
            for doc in store.documents()
        ]

    def write_candidate(self, candidate: MemoryCandidate) -> bool:
        from hermes.platform.memory.okf import OKFStore

        store = OKFStore(self.okf_dir)
        # Idempotência por CONTEÚDO: se a lição já está canônica (ex.: o staging ou
        # os instintos foram perdidos/restaurados e a lição voltou a ser promovida),
        # aponta para o documento existente em vez de duplicar a lição na árvore.
        for existing in store.documents():
            if existing.body.strip() == candidate.fact.strip():
                self.last_written = existing.filepath
                return True

        doc = store.save_document(
            title=f"Licao da Sessao {self.session_id[:8]}",
            content=candidate.fact,
            doc_type="concept",
            tags=["session", "dream", "auto-extracted", "promoted"],
            filename=f"lesson_{self.session_id[-6:]}.md",
            extra_metadata={
                "source_session": self.session_id,
                "session_title": self.session_title,
                "destination": candidate.proposed_destination,
                "confidence": candidate.confidence,
                "provenance": list(candidate.provenance),
            },
        )
        self.last_written = doc.filepath
        return True


class DreamConsolidator:
    """Orchestrates memory consolidation across recent sessions."""

    def __init__(self, hermes_home: Optional[Path] = None):
        from hermes_constants import get_hermes_home  # function-level: lint A6

        self.home = (hermes_home or Path(get_hermes_home())).resolve()
        self.memory_dir = self.home / "memory"
        # Caminhos CANÔNICOS do nó, não subpastas de memory/: são estes que os
        # leitores usam (tools/haos_memory_tools.py, hybrid_router, status do
        # haos-edge). Escrevendo em memory/okf e memory/vault/adrs o dream
        # produzia lições e ADRs que NENHUM leitor enxergava — o vault canônico é
        # <home>/obsidian_vault e o OKF é <home>/okf.
        self.okf_dir = self.home / "okf"
        self.vault_adrs_dir = self.home / "obsidian_vault" / "adrs"
        self.cursor_file = self.memory_dir / ".dream_cursor"
        self.git_store = DreamGitStore(self.memory_dir)
        self.reconciler = MemoryReconciler(self.memory_dir / "reconciled_memories.db")

        # Pipeline de memória (P11): staging persistente + roteamento determinístico.
        self.staging_store = MemoryStagingStore(self.memory_dir / "staging")
        self.router = MemoryRouter(staging_store=self.staging_store)
        self.instinct_store = InstinctStore(self.memory_dir / "instincts")

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
        """Consolidate unconsolidated sessions since last cursor.

        P11 — gate de staging: cada lição extraída vira um ``MemoryCandidate``
        (MemoryRouter.route_fact → classificação determinística) com confiança
        derivada do InstinctStore (0.3 na primeira aparição, +0.2 por recorrência
        entre sessões). A lição só chega à árvore canônica (okf/) quando PROMOVIDA:
        confiança >= MemoryCandidate.is_high_confidence() (0.85, via
        MemoryRouter.process_candidate). Abaixo disso ela fica PERSISTIDA como
        ``pending`` no MemoryStagingStore, com proveniência (id da sessão) e
        confiança. ``dry_run=True`` não escreve absolutamente nada.
        """
        if not dry_run:
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
                "staged_count": 0,
                "promoted_count": 0,
                "commit": None,
            }

        consolidated = 0
        staged = 0
        promoted = 0
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

            # Extração determinística da lição: primeiro período do preview (mesma
            # evidência estrutural de antes — frase completa; SEM gate por palavra-
            # chave solta no preview cru, que sumiu com o P11).
            lesson = preview.split(".")[0].strip()[:140]
            if len(lesson) <= 15:
                continue

            session_uri = f"session://{sid}"
            key = candidate_key(lesson)

            # Reforço é recorrência ENTRE sessões: a mesma sessão reprocessada não
            # reforça, e lição já promovida não é re-ingerida.
            existing = self.staging_store.get(key)
            if existing is not None:
                if existing.get("status") == PROMOTED or session_uri in (existing.get("provenance") or []):
                    continue

            # Destino determinístico (MemoryRouter.classify_destination) — usado para
            # a categoria do instinto e para a metadata da lição promovida.
            destination = self.router.classify_destination(lesson)

            if not dry_run:
                instinct = self.instinct_store.record_instinct(
                    rule=lesson,
                    category=_CATEGORY_BY_DESTINATION.get(destination, "workflow"),
                    project_scope=INSTINCT_PROJECT_SCOPE,
                    tags=["dream-distilled"],
                )
                confidence = instinct.confidence
            else:
                # Dry-run: prevê a confiança do próximo reforço sem escrever nada.
                confidence = self.instinct_store.peek_confidence(
                    rule=lesson, project_scope=INSTINCT_PROJECT_SCOPE
                )

            candidate = self.router.route_fact(
                fact=lesson,
                source_uri=session_uri,
                confidence=confidence,
                scope="project",
            )
            consolidated += 1

            if dry_run:
                continue

            writer = _CanonicalLessonWriter(okf_dir=self.okf_dir, session_id=sid, title=title)
            if self.router.process_candidate(candidate, writer):
                # Acumula a proveniência da sessão da promoção ANTES de marcar,
                # para o registro refletir todas as sessões que viram a lição.
                self.staging_store.stage_candidate(candidate, reason="recurrence_promotion")
                promoted += 1
                self.staging_store.mark_promoted(
                    key, doc_path=str(writer.last_written) if writer.last_written else None
                )

                # Mem0-inspired declarative memory reconciliation (apenas no que é
                # promovido, como antes: o stats espelha conhecimento canônico).
                if any(k in preview.lower() for k in ("preferência", "preferencia", "usando", "migramos", "banco", "framework", "agora usamos", "não usamos")):
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
            else:
                staged += 1
                if candidate.status == "rejected":
                    # Conflito com conhecimento canônico: nada é descartado — o
                    # candidato volta para o staging até reforçar/resolver.
                    self.router.stage_candidate(candidate, reason=STAGE_REASON_CONFLICT)

        if dry_run:
            return {
                "status": "dry_run",
                "consolidated_count": consolidated,
                "staged_count": staged,
                "promoted_count": promoted,
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
            "staged_count": staged,
            "promoted_count": promoted,
            "reconciliation": reconciliation_stats,
            "commit": commit_info.sha if commit_info else None,
            "timestamp": commit_info.timestamp if commit_info else None,
        }
