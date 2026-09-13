"""Kilo Lane Adapter — worker lane de código sobre o workspace canônico (K8).

A lane ``kilo`` executa em um workspace **canônico do card** (scratch sob o
root gerenciado ou worktree linked real), nunca em dir avulso ``/tmp``.
O adapter é fino: o workspace é resolvido pelo ``WorkspaceManager`` sobre o
Kanban upstream (mesmo DB do ``KanbanAdapter``), e a remoção canônica é dona
do kernel em ``complete_task``.

``execute_assignment`` é o caminho demo/determinístico: resolve o workspace do
card e devolve o resultado estruturado da lane. O caminho real de execução
(dispatcher + worker lanes) usa o ``WorkspaceManager``/``claim_tick`` — este
adapter existe para os fluxos que pedem o resultado da lane diretamente.
"""

from typing import Dict, Any, Optional

from hermes.platform.execution.assignment import AssignmentSpec
from hermes.platform.workspaces.manager import WorkspaceManager, WorkspaceError


class KiloLaneAdapter:
    """Worker Lane Adapter for Kilo coding workers in canonical workspaces."""

    def __init__(self, workspace_manager: Optional[WorkspaceManager] = None,
                 *, db_path: Optional[str] = None, board: Optional[str] = None):
        """Bind a canonical DB. ``db_path``/``board`` espelham o
        ``KanbanAdapter``; sem eles o manager usa a resolução upstream do board
        (env). Um manager pronto também pode ser injetado."""
        self.workspace_manager = workspace_manager or WorkspaceManager(db_path=db_path, board=board)

    def execute_assignment(self, assignment: AssignmentSpec) -> Dict[str, Any]:
        # Workspace canônico do card do assignment (T-... ou t_...). Erro claro
        # se o card não existe no DB canônico (save_task primeiro).
        try:
            ws_path = self.workspace_manager.create_workspace(assignment.task_id)
        except WorkspaceError as exc:
            raise WorkspaceError(
                f"Kilo lane cannot resolve a workspace for task "
                f"{assignment.task_id!r}: {exc}"
            ) from exc

        return {
            "status": "COMPLETED",
            "run_id": assignment.run_id,
            "lane": assignment.lane or "kilo",
            "workspace_path": ws_path,
            "workspace_uri": assignment.workspace_uri or ws_path,
            "summary": f"Executed coding task in Kilo lane at canonical workspace {ws_path}",
            "artifacts": [f"patch_{assignment.task_id}.diff"],
        }
