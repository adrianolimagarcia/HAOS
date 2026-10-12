"""CLI commands for managing declarative Team Templates."""

from __future__ import annotations

import argparse
import json
from typing import List, Optional

from hermes.platform.teams.registry import (
    list_team_templates,
    get_team_template,
    execute_sequential_team,
    export_team_template_markdown,
    import_team_template_markdown,
    get_user_teams_dir,
)
from hermes.platform.teams.schema import TeamRole


def handle_team_command(args: argparse.Namespace) -> int:
    subcmd = getattr(args, "team_subcommand", None)

    if subcmd == "list":
        templates = list_team_templates()
        if not templates:
            print("Nenhum template de time encontrado.")
            return 0
        print(f"Templates de time disponíveis ({len(templates)}):")
        for t in templates:
            roles_str = ", ".join(r.name for r in t.roles)
            print(f"- {t.name:<24} | Papéis: [{roles_str}] | {t.description}")
        return 0

    elif subcmd == "show":
        name = getattr(args, "name", None)
        if not name:
            print("Erro: especifique o nome do time.")
            return 1
        try:
            t = get_team_template(name)
            print(f"Time: {t.name}")
            print(f"Descrição: {t.description}")
            print(f"Modo de Coordenação: {t.coordination.mode.value}")
            print(f"Contexto Compartilhado: {t.coordination.shared_context}")
            print("\nPapéis:")
            for r in t.roles:
                denied = f" (proibidas: {', '.join(r.tools_denied)})" if r.tools_denied else ""
                allowed = f" (permitidas: {', '.join(r.tools_allowed)})" if r.tools_allowed else ""
                print(f"  • {r.name}: max_iterations={r.max_iterations}{denied}{allowed}")
                print(f"    Prompt: {r.system_prompt[:80]}...")
            return 0
        except FileNotFoundError:
            print(f"Erro: template '{name}' não encontrado.")
            return 1

    elif subcmd == "run":
        name = getattr(args, "name", None)
        goal = getattr(args, "goal", "")
        if not name or not goal:
            print("Erro: informe o nome do time e o objetivo ('goal').")
            return 1
        try:
            t = get_team_template(name)
            print(f"Iniciando time '{t.name}' com objetivo: {goal}")

            def _dummy_executor(role: TeamRole, g: str, ctx: dict):
                print(f"  -> Executando papel '{role.name}'...")
                return {"status": "ok", "role": role.name, "output": f"Resultado do papel {role.name}"}

            res = execute_sequential_team(t, goal, _dummy_executor)
            print("Execução concluída:")
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        except Exception as e:
            print(f"Erro na execução do time: {e}")
            return 1

    elif subcmd == "export":
        name = getattr(args, "name", None)
        output_file = getattr(args, "output", None)
        if not name:
            print("Erro: especifique o nome do time para exportar.")
            return 1
        try:
            t = get_team_template(name)
            md_text = export_team_template_markdown(t)
            if output_file:
                from pathlib import Path
                Path(output_file).write_text(md_text, encoding="utf-8")
                print(f"Template do time '{name}' exportado com sucesso para {output_file}")
            else:
                print(md_text)
            return 0
        except FileNotFoundError:
            print(f"Erro: template '{name}' não encontrado.")
            return 1
        except Exception as e:
            print(f"Erro ao exportar template: {e}")
            return 1

    elif subcmd == "import":
        file_path = getattr(args, "file", None)
        inert = not getattr(args, "active", False)
        if not file_path:
            print("Erro: especifique o caminho do arquivo Markdown para importar.")
            return 1
        try:
            from pathlib import Path
            import yaml
            p = Path(file_path)
            if not p.exists():
                print(f"Erro: arquivo '{file_path}' não encontrado.")
                return 1
            content = p.read_text(encoding="utf-8")
            template, _ = import_team_template_markdown(content, inert=inert)

            dest_dir = get_user_teams_dir()
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / f"{template.name}.yaml"
            data = template.model_dump(exclude_none=True)
            dest_file.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
            print(f"Time '{template.name}' importado com sucesso (inércia={'SIM' if inert else 'NÃO'}) em {dest_file}")
            return 0
        except Exception as e:
            print(f"Erro ao importar template de time: {e}")
            return 1

    print("Subcomando de time não reconhecido. Use: list, show, run, export, import")
    return 1
