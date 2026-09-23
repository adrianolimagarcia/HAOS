"""The Eye Dispatch Tool: Centralizes mission orchestration via The Eye (Master Architect).
Coordinates with Orquestrador and dispatches the specialist bot fleet (Forge Coder,
Pixel Front, Verifier QA, etc.) in dedicated worktrees with full DAG lineage.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.registry import registry
from tools.file_tools_paths import _resolve_base_dir

logger = logging.getLogger("tools.the_eye_dispatch")

THE_EYE_DISPATCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "the_eye_dispatch",
        "description": (
            "Aciona a frota autônoma de bots através do Arquiteto Supremo (The Eye). "
            "Use quando o usuário solicitar 'use bots', 'the eye', 'eye' ou 'theeye'. "
            "The Eye recebe a missão, projeta as especificações técnicas, aciona o Orquestrador "
            "e delega a execução aos bots especialistas (Forge Coder, Verifier QA, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mission": {
                    "type": "string",
                    "description": "Objetivo ou missão de engenharia/arquitetura a ser entregue pela frota."
                },
                "priority": {
                    "type": "integer",
                    "description": "Prioridade no Kanban (default: 90, range: 1-100).",
                    "default": 90
                },
                "require_qa": {
                    "type": "boolean",
                    "description": "Se verdadeiro, exige homologação pelo bot Verifier QA antes de concluir.",
                    "default": True
                },
                "yolo_mode": {
                    "type": "boolean",
                    "description": "Modo autônomo total (sem paradas para confirmação).",
                    "default": True
                }
            },
            "required": ["mission"]
        }
    }
}


def the_eye_dispatch_handler(
    mission: str,
    priority: int = 90,
    require_qa: bool = True,
    yolo_mode: bool = True,
    **kw: Any
) -> str:
    """Executa a missão agêntica através da hierarquia The Eye -> Orquestrador -> Especialistas."""
    start_time = time.monotonic()
    mission = (mission or "").strip()
    if not mission:
        return json.dumps({"ok": False, "error": "Missão não pode ser vazia."})

    eye_soul_path = Path("/root/.haos/profiles/the_eye/SOUL.md")
    if not eye_soul_path.is_file():
        return json.dumps({"ok": False, "error": "Perfil the_eye/SOUL.md não encontrado."})

    logger.info("The Eye Dispatch acionado para a missão: %s", mission)

    # 1. Cria a tarefa no Kanban vinculada ao The Eye
    task_id = f"eye-{int(time.time()) % 1000000:06d}"

    try:
        from hermes_cli import kanban_db as kb

        db_path = Path("/root/.haos/kanban.db")
        if not db_path.exists():
            db_path = Path(os.environ.get("HAOS_HOME", "/root/.haos")) / "kanban.db"

        # Tenta criar o card no Kanban via rotina canônica se o banco estiver acessível
        spec_data = {
            "agent_profile": "the_eye",
            "bot_id": "the_eye",
            "role": "master",
            "posture": "executive",
            "require_qa": require_qa,
            "yolo_mode": yolo_mode,
            "review_stages": ["verifier_qa"] if require_qa else [],
        }

        with kb._open_db(db_path) as conn:
            desc = (
                f"Missão executiva despachada pelo The Eye para coordenação com Orquestrador:\n\n"
                f"{mission}\n\n"
                f"Diretrizes:\n"
                f"- Orquestrador deve quebrar em sub-tarefas para especialistas (Forge Coder, etc.)\n"
                f"- Exigir evidência observada e testes antes de homologar (Verifier QA: {require_qa})"
            )
            created_id = kb.create_task(
                conn,
                title=f"👑 [The Eye] {mission[:80]}",
                body=desc,
                assignee="the_eye",
                created_by="the_eye",
                priority=priority,
            )
            if created_id:
                task_id = created_id
    except Exception as e:
        logger.warning("Falha ao registrar card formal no kanban.db (seguindo em memória): %s", e)

    # 2. Prepara o contexto de liderança do The Eye
    eye_soul = eye_soul_path.read_text(encoding="utf-8")

    # 3. Dispara a coordenação com a ferramenta delegate_task para a árvore de execução
    from tools.delegate_tool import delegate_task as raw_delegate

    # Subagente 1: The Eye elaborando o plano e formalizando com o Orquestrador
    orch_task = {
        "goal": (
            f"[THE EYE -> ORQUESTRADOR] Analise a missão: '{mission}'. "
            f"Como The Eye (Master Supremo), elabore o plano arquitetural de alto nível e as diretrizes invariantes. "
            f"Em seguida, como Orquestrador (Manager), divida a execução entre os especialistas "
            f"(Forge Coder para código, Verifier QA para testes). "
            f"Se require_qa={require_qa}, valide a conformidade antes de homologar."
        ),
        "context": (
            f"{eye_soul}\n\n"
            f"--- Contexto Operacional ---\n"
            f"Missão: {mission}\n"
            f"Task ID no Kanban: {task_id}\n"
            f"YOLO Mode: {yolo_mode}\n"
            f"Require QA: {require_qa}\n"
        )
    }

    try:
        # Executa a delegação hierárquica
        res_json_str = raw_delegate(
            tasks=[orch_task],
            parent_agent=kw.get("parent_agent"),
            background=False
        )
        deleg_res = json.loads(res_json_str) if isinstance(res_json_str, str) else res_json_str
    except Exception as exc:
        logger.exception("Erro durante a execução do the_eye_dispatch: %s", exc)
        return json.dumps({
            "ok": False,
            "task_id": task_id,
            "error": f"Falha na delegação do The Eye: {exc}"
        }, ensure_ascii=False)

    elapsed = time.monotonic() - start_time

    return json.dumps({
        "ok": True,
        "engine": "the_eye_fleet_orchestration",
        "task_id": task_id,
        "mission": mission,
        "master": "the_eye",
        "manager": "orquestrador",
        "require_qa": require_qa,
        "elapsed_seconds": round(elapsed, 2),
        "delegation_outcome": deleg_res
    }, ensure_ascii=False)


registry.register(
    name="the_eye_dispatch",
    toolset="coding",
    schema=THE_EYE_DISPATCH_SCHEMA,
    handler=lambda args, **kw: the_eye_dispatch_handler(
        mission=args.get("mission", ""),
        priority=int(args.get("priority", 90)),
        require_qa=bool(args.get("require_qa", True)),
        yolo_mode=bool(args.get("yolo_mode", True)),
        **kw,
    ),
    check_fn=lambda: os.path.isfile("/root/.haos/profiles/the_eye/SOUL.md"),
    emoji="👑",
)
