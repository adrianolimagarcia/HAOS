"""Módulo de Orquestração de Sombras / Shadow Leafs (Agentes Folha Descartáveis em Background).

Conceito Chave (Arquitetura Ultra-SOTA):
- As Sombras (Shadow Leafs) funcionam exatamente como Subagentes Autônomos em Background.
- Ao serem invocadas, liberam imediatamente o agente titular (pai) para que ele continue operando em outras tarefas em paralelo.
- A Sombra executa em um Git Worktree descartável isolado, herdando 100% da identidade/SOUL do bot pai.
- Executa a missão em thread de background com captura de logs, saída estruturada e ciclo de vida completo (active -> completed/failed -> discarded).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ShadowLeaf:
    leaf_id: str
    parent_bot_id: str
    parent_bot_name: str
    profile: str
    task_description: str
    worktree_path: str
    branch_name: str
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    status: str = "active"  # active, completed, discarded, failed
    exit_code: Optional[int] = None
    summary: str = ""
    last_log: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ShadowLeafManager:
    """Gerencia o ciclo de vida e a execução assíncrona das Sombras (Shadow Leafs)."""

    def __init__(self, base_repo_dir: Path, shadows_root: Optional[Path] = None):
        self.base_repo_dir = Path(base_repo_dir).resolve()
        self.shadows_root = Path(shadows_root or "/tmp/haos-shadows").resolve()
        self.shadows_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._registry_file = self.shadows_root / "shadow_leaves_registry.json"
        self._leaves: dict[str, ShadowLeaf] = self._load_registry()
        self._threads: dict[str, threading.Thread] = {}

    def _load_registry(self) -> dict[str, ShadowLeaf]:
        if not self._registry_file.is_file():
            return {}
        try:
            raw = json.loads(self._registry_file.read_text(encoding="utf-8"))
            result = {}
            for k, v in raw.items():
                result[k] = ShadowLeaf(**v)
            return result
        except Exception as e:
            logger.warning("Falha ao carregar registro de sombras: %s", e)
            return {}

    def _save_registry(self) -> None:
        try:
            raw = {k: v.to_dict() for k, v in self._leaves.items()}
            self._registry_file.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.error("Erro ao salvar registro de sombras: %s", e)

    def spawn_shadow(
        self,
        parent_bot_id: str,
        parent_bot_name: str,
        profile: str,
        task_description: str,
        custom_leaf_id: Optional[str] = None,
        base_commit: str = "HEAD",
        metadata: Optional[dict[str, Any]] = None,
        executor_fn: Optional[Callable[[ShadowLeaf], None]] = None,
        run_in_background: bool = True,
    ) -> ShadowLeaf:
        """Cria, isola em Git Worktree e despacha a Sombra em background para execução paralela."""
        with self._lock:
            ts = int(time.time())
            safe_parent = re.sub(r'[^a-zA-Z0-9_\-]+', '', parent_bot_id)
            leaf_id = custom_leaf_id or f"shadow-{safe_parent}-{ts}"
            branch_name = f"shadow/{leaf_id}"
            worktree_dir = self.shadows_root / leaf_id

            # Tentativa de Aceleração Nativa em Rust via haos-edge
            spawned_by_rust = False
            try:
                import urllib.request
                req_data = json.dumps({
                    "repo_dir": str(self.base_repo_dir),
                    "parent_bot_id": safe_parent,
                    "custom_leaf_id": leaf_id,
                    "base_commit": base_commit
                }).encode("utf-8")
                req = urllib.request.Request(
                    "http://127.0.0.1:8788/api/worktree/spawn",
                    data=req_data,
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=0.5) as resp:
                    rdata = json.loads(resp.read().decode("utf-8"))
                    if rdata.get("ok"):
                        spawned_by_rust = True
            except Exception:
                pass

            if not spawned_by_rust:
                if worktree_dir.exists():
                    shutil.rmtree(worktree_dir, ignore_errors=True)

                # Executa git worktree add (fallback)
                cmd = [
                    "git",
                    "-C",
                    str(self.base_repo_dir),
                    "worktree",
                    "add",
                    "-b",
                    branch_name,
                    str(worktree_dir),
                    base_commit,
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    raise RuntimeError(f"Falha ao criar git worktree para sombra {leaf_id}: {res.stderr}")

            # Registra a Sombra com herança total
            leaf = ShadowLeaf(
                leaf_id=leaf_id,
                parent_bot_id=parent_bot_id,
                parent_bot_name=parent_bot_name,
                profile=profile,
                task_description=task_description,
                worktree_path=str(worktree_dir),
                branch_name=branch_name,
                created_at=time.time(),
                status="active",
                metadata=metadata or {},
            )
            self._leaves[leaf_id] = leaf
            self._save_registry()

            # Execução de Subagente em Background
            target_fn = executor_fn or self._default_leaf_worker
            if run_in_background:
                t = threading.Thread(
                    target=self._run_leaf_wrapper,
                    args=(leaf, target_fn),
                    name=f"ShadowLeafThread-{leaf_id}",
                    daemon=True,
                )
                self._threads[leaf_id] = t
                t.start()
            else:
                self._run_leaf_wrapper(leaf, target_fn)

            return leaf

    def _default_leaf_worker(self, leaf: ShadowLeaf) -> None:
        """Worker padrão da Sombra: executa verificação de ambiente e prepara a sandbox."""
        logger.info("[ShadowLeaf %s] Inicializando worker isolado no worktree: %s", leaf.leaf_id, leaf.worktree_path)
        time.sleep(0.5)
        # Registra sucesso da alocação autônoma
        leaf.last_log = f"Ambiente isolado pronto. Workspace: {leaf.worktree_path}"
        leaf.summary = f"Sombra executando tarefa em paralelo: {leaf.task_description}"

    def _run_leaf_wrapper(self, leaf: ShadowLeaf, worker_fn: Callable[[ShadowLeaf], None]) -> None:
        try:
            worker_fn(leaf)
            with self._lock:
                if leaf.status == "active":
                    leaf.status = "completed"
                leaf.completed_at = time.time()
                self._save_registry()
        except Exception as exc:
            logger.exception("[ShadowLeaf %s] Falha na execução da sombra: %s", leaf.leaf_id, exc)
            with self._lock:
                leaf.status = "failed"
                leaf.last_log = str(exc)
                leaf.completed_at = time.time()
                self._save_registry()

    def list_shadows(self, parent_bot_id: Optional[str] = None) -> list[ShadowLeaf]:
        with self._lock:
            leaves = list(self._leaves.values())
            if parent_bot_id:
                leaves = [l for l in leaves if l.parent_bot_id == parent_bot_id]
            leaves.sort(key=lambda x: x.created_at, reverse=True)
            return leaves

    def get_shadow(self, leaf_id: str) -> Optional[ShadowLeaf]:
        with self._lock:
            return self._leaves.get(leaf_id)

    def discard_shadow(self, leaf_id: str, force: bool = True) -> bool:
        """Descarta o git worktree e o branch da sombra, liberando recursos com segurança."""
        with self._lock:
            leaf = self._leaves.get(leaf_id)
            if not leaf:
                return False

            # Aciona cancelamento assíncrono no haos-edge se a sombra estiver em voo
            try:
                import urllib.request
                creq = urllib.request.Request(
                    "http://127.0.0.1:8788/api/cancel/trigger",
                    data=json.dumps({"id": leaf_id}).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(creq, timeout=0.2):
                    pass
            except Exception:
                pass

            worktree_dir = Path(leaf.worktree_path)

            # Tentativa de descarte acelerado via haos-edge
            discarded_by_rust = False
            try:
                import urllib.request
                req_data = json.dumps({
                    "repo_dir": str(self.base_repo_dir),
                    "worktree_path": str(worktree_dir),
                    "branch_name": leaf.branch_name
                }).encode("utf-8")
                req = urllib.request.Request(
                    "http://127.0.0.1:8788/api/worktree/discard",
                    data=req_data,
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=0.5) as resp:
                    rdata = json.loads(resp.read().decode("utf-8"))
                    if rdata.get("ok"):
                        discarded_by_rust = True
            except Exception:
                pass

            if not discarded_by_rust:
                # 1. git worktree remove (fallback)
                if worktree_dir.exists():
                    cmd = ["git", "-C", str(self.base_repo_dir), "worktree", "remove", "--force", str(worktree_dir)]
                    subprocess.run(cmd, capture_output=True, text=True)

                if worktree_dir.exists():
                    shutil.rmtree(worktree_dir, ignore_errors=True)

                # 2. Deleta o branch temporário
                if leaf.branch_name:
                    cmd_branch = ["git", "-C", str(self.base_repo_dir), "branch", "-D", leaf.branch_name]
                    subprocess.run(cmd_branch, capture_output=True, text=True)

            leaf.status = "discarded"
            self._save_registry()
            return True

    def delete_record(self, leaf_id: str) -> bool:
        with self._lock:
            self.discard_shadow(leaf_id)
            if leaf_id in self._leaves:
                del self._leaves[leaf_id]
                self._save_registry()
                return True
            return False
