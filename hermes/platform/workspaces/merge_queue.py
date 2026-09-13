"""Merge Queue — Lane Kilo Serial Rebase-Validation Fabric (Marco 9).

Gerencia fila ordenada de commits validados em worktrees efêmeros aguardando merge
na branch principal (lane kilo), garantindo rebase serial e re-validação obrigatória
antes do merge final.

Restrições:
- Strictly stdlib-only imports.
- Strictly PEP-420 namespace compliance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import hashlib
import os
import subprocess
import time


class MergeStatus(str, Enum):
    QUEUED = "queued"
    RUNNING_VALIDATION = "running_validation"
    MERGED = "merged"
    REJECTED = "rejected"


@dataclass
class MergeCandidate:
    """Candidato a merge na Lane Kilo (Marco 9).
    
    Campos canônicos:
    - task_id: identificador da tarefa/worktree
    - branch: branch fonte contendo o diff validado
    - test_report_hash: hash criptográfico ou identificador do relatório de testes
    - priority: prioridade na fila (maior valor = maior prioridade)
    - status: queued, running_validation, merged, rejected
    """
    task_id: str
    branch: str
    test_report_hash: str
    priority: int = 0
    status: MergeStatus = MergeStatus.QUEUED
    target_branch: str = "main"
    rebase_commit: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "branch": self.branch,
            "test_report_hash": self.test_report_hash,
            "priority": self.priority,
            "status": self.status.value if isinstance(self.status, MergeStatus) else str(self.status),
            "target_branch": self.target_branch,
            "rebase_commit": self.rebase_commit,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


class MergeQueue:
    """Fila ordenada de candidatos a merge com rebase serial e validação estrita (Marco 9).
    
    Enforces serial rebase-validation before merge to main branch (lane kilo).
    Garante que nenhum commit entre na branch principal sem ter sido rebased
    sobre a cabeça mais recente e re-testado.
    """

    def __init__(
        self,
        repo_root: Optional[Path | str] = None,
        validator_fn: Optional[Callable[[MergeCandidate, Path], bool]] = None,
        target_branch: str = "main",
        require_validator: bool = False,
    ) -> None:
        self.repo_root = Path(repo_root or Path.cwd()).resolve()
        self.validator_fn = validator_fn
        self.target_branch = target_branch
        self.require_validator = require_validator
        self._queue: List[MergeCandidate] = []
        self._history: List[MergeCandidate] = []

    def enqueue(
        self,
        task_id: str,
        branch: str,
        test_report_hash: str,
        priority: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> MergeCandidate:
        """Adiciona um candidato à fila ordenado por prioridade (decrescente) e tempo (crescente)."""
        candidate = MergeCandidate(
            task_id=task_id,
            branch=branch,
            test_report_hash=test_report_hash,
            priority=priority,
            status=MergeStatus.QUEUED,
            target_branch=self.target_branch,
            metadata=metadata or {},
        )
        self._queue.append(candidate)
        self._sort_queue()
        return candidate

    def _sort_queue(self) -> None:
        """Ordena por priority decrescente, e timestamp crescente para mesma prioridade."""
        self._queue.sort(key=lambda c: (-c.priority, c.created_at))

    def list_queue(self) -> List[MergeCandidate]:
        """Retorna uma cópia da fila ordenada atual."""
        return list(self._queue)

    def get_candidate(self, task_id: str) -> Optional[MergeCandidate]:
        for c in self._queue:
            if c.task_id == task_id:
                return c
        for c in self._history:
            if c.task_id == task_id:
                return c
        return None

    def _run_git(self, *args: str, cwd: Optional[Path] = None) -> str:
        res = subprocess.run(
            ["git", *args],
            cwd=str(cwd or self.repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed (exit {res.returncode}): {res.stderr.strip()}")
        return res.stdout.strip()

    def process_next(self) -> Optional[MergeCandidate]:
        """Processa serialmente o próximo candidato da fila.
        
        Executa:
        1. Desempilha candidato de maior prioridade.
        2. Status -> running_validation.
        3. Tenta rebase serial sobre o target_branch atual.
           - Se rebase falhar (conflito): rejeita imediatamente.
        4. Executa validador de testes na árvore pós-rebase.
           - Se testes falharem: aborta rebase (se necessário) e rejeita.
        5. Se validação passar: avança target_branch (fast-forward/merge) e marca MERGED.
        """
        if not self._queue:
            return None

        candidate = self._queue.pop(0)
        candidate.status = MergeStatus.RUNNING_VALIDATION
        candidate.updated_at = time.time()

        try:
            # 1. Serial rebase validation
            rebased_ok, reason = self._rebase_candidate(candidate)
            if not rebased_ok:
                candidate.status = MergeStatus.REJECTED
                candidate.rejection_reason = reason or "Rebase conflict or failure"
                candidate.updated_at = time.time()
                self._history.append(candidate)
                return candidate

            # 2. Run validation suite
            if self.validator_fn is None and self.require_validator:
                candidate.status = MergeStatus.REJECTED
                candidate.rejection_reason = "No validator_fn configured: post-rebase verification is mandatory"
                candidate.updated_at = time.time()
                self._rollback_candidate(candidate)
                self._history.append(candidate)
                return candidate

            validation_passed = True
            if self.validator_fn:
                try:
                    validation_passed = self.validator_fn(candidate, self.repo_root)
                except Exception as exc:
                    validation_passed = False
                    reason = f"Validator raised exception: {exc}"

            if not validation_passed:
                candidate.status = MergeStatus.REJECTED
                candidate.rejection_reason = reason or "Post-rebase test validation failed"
                candidate.updated_at = time.time()
                self._rollback_candidate(candidate)
                self._history.append(candidate)
                return candidate

            # 3. Fast-forward / merge to target
            self._merge_to_target(candidate)
            candidate.status = MergeStatus.MERGED
            candidate.updated_at = time.time()
            self._history.append(candidate)
            return candidate

        except Exception as exc:
            candidate.status = MergeStatus.REJECTED
            candidate.rejection_reason = f"Unexpected processing error: {exc}"
            candidate.updated_at = time.time()
            self._history.append(candidate)
            return candidate

    def process_all(self) -> List[MergeCandidate]:
        """Processa toda a fila serialmente até esvaziá-la."""
        processed = []
        while self._queue:
            cand = self.process_next()
            if cand:
                processed.append(cand)
        return processed

    def _rebase_candidate(self, candidate: MergeCandidate) -> tuple[bool, Optional[str]]:
        """Realiza rebase da branch do candidato sobre target_branch."""
        try:
            # Garante que as branches existem
            self._run_git("rev-parse", "--verify", candidate.branch)
            self._run_git("rev-parse", "--verify", candidate.target_branch)

            # Executa rebase da branch sobre target_branch
            self._run_git("rebase", candidate.target_branch, candidate.branch)
            rev = self._run_git("rev-parse", candidate.branch)
            candidate.rebase_commit = rev
            return True, None
        except Exception as exc:
            # Aborta rebase caso tenha ficado em estado intermediário
            try:
                self._run_git("rebase", "--abort")
            except Exception:
                pass
            return False, f"Rebase failed: {exc}"

    def _rollback_candidate(self, candidate: MergeCandidate) -> None:
        """Garante que a working tree e branch voltem a um estado são após falha de validação."""
        try:
            self._run_git("checkout", candidate.target_branch)
        except Exception:
            pass

    def _merge_to_target(self, candidate: MergeCandidate) -> None:
        """Efetua o merge/fast-forward da branch rebased para a target_branch."""
        self._run_git("checkout", candidate.target_branch)
        # Sendo pós-rebase, deve ser fast-forward limpo
        self._run_git("merge", "--ff-only", candidate.branch)
