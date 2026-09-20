"""Tool para orquestração autônoma de Sombras / Shadow Leafs (Subagentes Descartáveis em Background).

Permite que qualquer agente especialista invoque automaticamente uma Sombra (agente folha descartável)
para tarefas em paralelo, liberando o agente titular para continuar atendendo a outras demandas.
"""

from pathlib import Path
from typing import Any, Optional

from tools.registry import registry
from hermes.platform.shadow_leaf import ShadowLeafManager

_manager: Optional[ShadowLeafManager] = None


def _get_manager() -> ShadowLeafManager:
    global _manager
    if _manager is None:
        _manager = ShadowLeafManager(base_repo_dir=Path.cwd())
    return _manager


@registry.register(
    name="spawn_shadow_leaf",
    description=(
        "Spawna um Subagente Sombra (Shadow Leaf) em background dentro de um Git Worktree isolado. "
        "O agente pai é liberado IMEDIATAMENTE para executar outras tarefas em paralelo, enquanto "
        "a Sombra assume a missão em seu próprio workspace independente com concorrência zero."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "Descrição da missão técnica independente que a Sombra deve executar em background.",
            },
            "parent_bot_id": {
                "type": "string",
                "description": "ID do bot titular que está despachando a Sombra (ex: 'forge_coder', 'sentinel_sre').",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Executar em background liberando o bot pai imediatamente (padrão: true).",
            },
        },
        "required": ["task_description"],
    },
)
def spawn_shadow_leaf(
    task_description: str,
    parent_bot_id: Optional[str] = None,
    run_in_background: bool = True,
) -> dict[str, Any]:
    mgr = _get_manager()
    p_id = parent_bot_id or "autonomous_agent"
    try:
        leaf = mgr.spawn_shadow(
            parent_bot_id=p_id,
            parent_bot_name=p_id.replace("_", " ").title(),
            profile=p_id,
            task_description=task_description,
            metadata={"spawn_mode": "subagent_background_leaf"},
            run_in_background=run_in_background,
        )
        return {
            "status": "success",
            "message": f"Subagente Sombra '{leaf.leaf_id}' spawnado em background com sucesso.",
            "leaf_id": leaf.leaf_id,
            "branch_name": leaf.branch_name,
            "worktree_path": leaf.worktree_path,
            "running_in_background": run_in_background,
            "note": (
                "Você (bot principal) está livre para continuar atendendo ao operador e realizando outras tarefas! "
                f"A Sombra '{leaf.leaf_id}' está trabalhando de forma independente no worktree isolado."
            )
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


@registry.register(
    name="discard_shadow_leaf",
    description=(
        "Descarta e remove fisicamente o Git Worktree de uma Sombra após a conclusão da missão, "
        "limpando o disco e o branch temporário."
    ),
    parameters={
        "type": "object",
        "properties": {
            "leaf_id": {
                "type": "string",
                "description": "ID da Sombra a ser descartada.",
            },
        },
        "required": ["leaf_id"],
    },
)
def discard_shadow_leaf(leaf_id: str) -> dict[str, Any]:
    mgr = _get_manager()
    try:
        ok = mgr.discard_shadow(leaf_id)
        if ok:
            return {
                "status": "success",
                "message": f"Sombra '{leaf_id}' foi descartada e seu Git Worktree removido fisicamente.",
                "leaf_id": leaf_id,
                "cleaned": True
            }
        return {
            "status": "error",
            "error": f"Sombra '{leaf_id}' não encontrada para descarte."
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }
