"""Workspace Fabric — K8 (Fase 0 do Mapa de Integrações).

O workspace de um card NÃO é um dir avulso em ``/tmp``: é uma propriedade do
card canônico do kernel (``workspace_kind``/``workspace_path`` na tabela
``tasks``). Este manager é um envoltório fino sobre a machinery upstream:

* ``create_workspace(task_id_or_spec)`` resolve o card canônico (aceita o id
  upstream ``t_...`` ou o id HAOS ``T-...`` via tabela ``haos_task_meta``) e
  delega a materialização a ``kanban_db_workspace.resolve_workspace(task,
  board=board)`` + ``set_workspace_path`` — scratch cai em
  ``<workspaces_root>/<task-id>``, ``dir`` no caminho absoluto do card,
  ``worktree`` em ``<repo>/.worktrees/<task-id>`` (ancorado no
  ``default_workdir`` do board quando o card não traz caminho próprio);
* ``cleanup_workspace(path)`` NUNCA remove fora do root de scratch gerenciado
  (mesmo modelo de contenção do kernel, ver ``kanban_db_workspace`` #28818);
  o ciclo de vida canônico de remoção (scratch archive / ``git worktree
  remove`` com guards de sujeira e push) é dono do kernel
  (``complete_task`` → ``_cleanup_workspace``), então o manager só remove o
  que ele mesmo criou sob um root gerenciado.

Regra v1.1: sem runtime externo; nenhum dir ``/tmp`` avulso. O card é a fonte
da verdade — o ``WorkspaceSpec`` é mantido por compat de API, mas o tipo real
vem do card (quem cria o card é o ``KanbanAdapter.save_task``, que mapeia
``TaskSpec.workspace_type`` → ``workspace_kind``).
"""

from pathlib import Path
import threading
from typing import Any, Dict, Optional

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_workspace as kbw


class WorkspaceError(RuntimeError):
    pass


class WorkspaceSpec:
    """Compat de API (consumidores legados constroem esta shape).

    O tipo efetivo de workspace é o do card canônico; ``type``/``uri``/
    ``base_branch`` ficam como metadados da intenção do chamador.
    """

    def __init__(self, uri: str = "", type: str = "scratch", base_branch: str = "main"):
        self.uri = uri
        self.type = type  # git_worktree | worktree | scratch | local (compat)
        self.base_branch = base_branch

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, WorkspaceSpec)
            and (self.uri, self.type, self.base_branch)
            == (other.uri, other.type, other.base_branch)
        )

    def __repr__(self) -> str:
        return f"WorkspaceSpec(type={self.type!r}, uri={self.uri!r}, base_branch={self.base_branch!r})"


class WorkspaceManager:
    """Cria/resolve workspaces canônicos de cards no Kanban upstream."""

    def __init__(self, db_path: Optional[str] = None, *, board: Optional[str] = None):
        """``db_path`` explícito (tests: arquivo temporário) ou resolução
        upstream do board (``HERMES_KANBAN_HOME`` / board atual). ``board`` é
        opcional e espelha o ``KanbanAdapter``."""
        self.db_path = Path(db_path) if db_path is not None else None
        self.board = board
        self._local = threading.local()  # conexão por thread (threadpool-safe)

    # ------------------------------------------------------------------ #
    # connection
    # ------------------------------------------------------------------ #
    def _connect(self):
        # Uma conexão POR THREAD (mesma regra do KanbanAdapter): sqlite3 é
        # thread-bound e o shell roda handlers num threadpool. Cache por
        # thread = mesmo modelo do plugin kanban upstream (conexão por
        # request) com menos open/close.
        conn = getattr(self._local, "conn", None)
        if conn is None:
            if self.db_path is not None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = kbc.connect(db_path=self.db_path, board=self.board)
            # Tabela aditiva HAOS (mesma do KanbanAdapter): o manager resolve
            # spec-id HAOS -> upstream t_... e precisa dela mesmo quando o
            # adapter ainda não inicializou esta conexão.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS haos_task_meta (
                    task_id     TEXT PRIMARY KEY,
                    spec_id     TEXT,
                    spec_json   TEXT,
                    phase       TEXT,
                    posture     TEXT,
                    revision    INTEGER,
                    run_json    TEXT,
                    result_json TEXT,
                    updated_at  REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_haos_meta_spec ON haos_task_meta(spec_id)")
            conn.commit()
            self._local.conn = conn
        return conn

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # ------------------------------------------------------------------ #
    # canonical id resolution (HAOS T-... <-> upstream t_...)
    # ------------------------------------------------------------------ #
    def _resolve_card_id(self, key: str) -> str:
        conn = self._connect()
        if kb.get_task(conn, key) is not None:
            return key
        row = conn.execute(
            "SELECT task_id FROM haos_task_meta WHERE spec_id = ?", (key,)
        ).fetchone()
        if row is None:
            raise WorkspaceError(
                f"task {key!r} not found in the canonical Kanban DB; save the "
                "TaskSpec first (KanbanAdapter.save_task) before asking for a workspace"
            )
        return row["task_id"]

    # ------------------------------------------------------------------ #
    # workspace lifecycle
    # ------------------------------------------------------------------ #
    def create_workspace(self, task_id: str, spec: Optional[WorkspaceSpec] = None) -> str:
        """Materializa (ou reusa) o workspace canônico do card e persiste o
        ``workspace_path`` no próprio card. Retorna o caminho absoluto.

        O card manda: ``resolve_workspace`` lê ``task.workspace_kind`` (scratch
        → root gerenciado; dir → caminho absoluto do card; worktree → worktree
        linked real). ``spec`` é aceito por compat e NÃO sobrescreve o card.
        """
        del spec  # card é a fonte da verdade (K8)
        task_id = self._resolve_card_id(task_id)
        conn = self._connect()
        task = kb.get_task(conn, task_id)
        if task is None:
            raise WorkspaceError(f"task {task_id!r} disappeared between resolve and use")
        path = kbw.resolve_workspace(task, board=self.board)
        kbw.set_workspace_path(conn, task.id, str(path))
        return str(path)

    def workspace_path(self, task_id: str) -> Optional[str]:
        """Caminho já resolvido no card (sem materializar)."""
        task_id = self._resolve_card_id(task_id)
        conn = self._connect()
        row = conn.execute(
            "SELECT workspace_kind, workspace_path FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None or not row["workspace_path"]:
            return None
        return str(row["workspace_path"])

    def cleanup_workspace(self, workspace_path: str) -> bool:
        """Remove um workspace de scratch APENAS se for descendente estrito de
        um root gerenciado (mesmo modelo de contenção do kernel).

        Worktree/dir NÃO são removidos aqui: a remoção canônica (com guards de
        sujeira/unpushed e archive) é dona do kernel em ``complete_task``.
        Devolve True se removeu, False se era fora de escopo (no-op seguro).
        """
        p = Path(workspace_path).expanduser()
        if not p.is_dir():
            return False
        root = kb.workspaces_root(board=self.board)
        if not _is_strict_descendant(p, root):
            return False  # nunca rmtree fora do scratch gerenciado (#28818)
        import shutil

        shutil.rmtree(p, ignore_errors=True)
        return True

    # ------------------------------------------------------------------ #
    # board metadata helper (worktree anchor: default_workdir do board)
    # ------------------------------------------------------------------ #
    def ensure_board_workdir(self, repo_root: str) -> Dict[str, Any]:
        """Grava ``default_workdir`` no board atual (metadados ``board.json``).
        Cards ``worktree`` sem ``workspace_path`` herdam este anchor. Usado
        pelos testes E2E e por quem quer worktrees sob um repo do board."""
        return kb.write_board_metadata(self.board, default_workdir=str(repo_root))


def _is_strict_descendant(p: Path, root: Path) -> bool:
    """True se *p* está estritamente dentro de *root* (p != root)."""
    try:
        p_resolved = p.resolve(strict=False)
        root_resolved = root.resolve(strict=False)
    except OSError:
        return False
    return root_resolved != p_resolved and root_resolved in p_resolved.parents
