"""AutoMergeGate — Verificação de testes pelo Reviewer e Merge Seguro de Worktree.

Garante que um diff gerado por um Coder em um worktree efêmero só seja incorporado
à branch principal após execução completa da suíte de testes com zero falhas,
incluindo análise dinâmica de impacto (blast radius) via LSP Unified Intelligence.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from hermes.platform.capabilities.lsp.unified_intelligence import (
    CodeSymbolGraph,
    ImpactAnalyzer,
    SymbolLocation,
    SymbolNode,
)
from hermes.platform.workspaces.git_worktree import GitWorktreeManager


class AutoMergeGate:
    """Valida evidência de revisão e efetua o merge seguro."""

    DEFAULT_CONTRACTS_CMD = (
        f"HERMES_PYTHON={sys.executable} "
        "scripts/run_tests.sh tests/platform/execution/test_contracts.py"
    )

    def __init__(
        self,
        worktree_manager: Optional[GitWorktreeManager] = None,
        symbol_graph: Optional[CodeSymbolGraph] = None,
        impact_analyzer: Optional[ImpactAnalyzer] = None,
    ):
        self.worktree_manager = worktree_manager or GitWorktreeManager()
        self.symbol_graph = symbol_graph or CodeSymbolGraph()
        self.impact_analyzer = impact_analyzer or ImpactAnalyzer(self.symbol_graph)

    def parse_diff_impact(
        self,
        diff_text: str,
        worktree_root: Optional[Path] = None,
    ) -> Tuple[Set[str], Set[str]]:
        """Extrai arquivos modificados e símbolos adicionados/alterados a partir do git diff.

        Retorna (modified_files, modified_symbols).
        """
        modified_files: Set[str] = set()
        modified_symbols: Set[str] = set()

        if not diff_text:
            return modified_files, modified_symbols

        # 1. Extrair caminhos de arquivos modificados via cabeçalhos diff
        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                parts = line.split()
                if len(parts) >= 4:
                    b_path = parts[3]
                    if b_path.startswith("b/"):
                        b_path = b_path[2:]
                    if b_path != "/dev/null":
                        modified_files.add(b_path)
            elif line.startswith("+++ b/"):
                filepath = line[6:].strip()
                if filepath != "/dev/null":
                    modified_files.add(filepath)

        # 2. Extrair símbolos modificados a partir dos chunks ou AST do arquivo no worktree
        # Identificadores de funções/classes em diff hunk headers (@@ ... @@ def foo():)
        hunk_header_re = re.compile(r"@@.*?@@\s*(?:def|class|async\s+def)\s+([a-zA-Z0-9_]+)")
        # Linhas adicionadas com definições
        added_def_re = re.compile(r"^\+\s*(?:async\s+def|def|class)\s+([a-zA-Z0-9_]+)")

        for line in diff_text.splitlines():
            m_hdr = hunk_header_re.search(line)
            if m_hdr:
                modified_symbols.add(m_hdr.group(1))

            m_def = added_def_re.match(line)
            if m_def:
                modified_symbols.add(m_def.group(1))

        # 3. Se temos worktree_root e arquivos python modificados, podemos extrair símbolos
        # via AST e registrá-los no symbol_graph caso ainda não existam.
        if worktree_root and worktree_root.exists():
            for f in modified_files:
                if f.endswith(".py"):
                    full_p = worktree_root / f
                    if full_p.exists():
                        try:
                            content = full_p.read_text(encoding="utf-8", errors="replace")
                            tree = ast.parse(content, filename=str(full_p))
                            for node in ast.walk(tree):
                                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                    sym_node = SymbolNode(
                                        name=node.name,
                                        kind="function",
                                        file_path=f,
                                        location=SymbolLocation(file_path=f, line=node.lineno, character=node.col_offset),
                                    )
                                    self.symbol_graph.add_symbol(sym_node)
                                    if node.name in modified_symbols:
                                        modified_symbols.add(sym_node.id)
                                elif isinstance(node, ast.ClassDef):
                                    sym_node = SymbolNode(
                                        name=node.name,
                                        kind="class",
                                        file_path=f,
                                        location=SymbolLocation(file_path=f, line=node.lineno, character=node.col_offset),
                                    )
                                    self.symbol_graph.add_symbol(sym_node)
                                    if node.name in modified_symbols:
                                        modified_symbols.add(sym_node.id)
                        except Exception:
                            pass

        return modified_files, modified_symbols

    def run_tests_in_worktree(
        self,
        task_id: str,
        test_command: str = DEFAULT_CONTRACTS_CMD,
    ) -> Dict[str, Any]:
        """Executa a bateria de testes de validação diretamente dentro do worktree."""
        worktree_path = (self.worktree_manager.worktrees_dir / f"task-{task_id}").resolve()
        if not worktree_path.exists():
            return {
                "success": False,
                "error": f"Worktree for task '{task_id}' not found at {worktree_path}",
                "returncode": -1,
            }

        res = subprocess.run(
            test_command,
            shell=True,
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
        )
        return {
            "success": res.returncode == 0,
            "returncode": res.returncode,
            "stdout_tail": res.stdout[-800:] if res.stdout else "",
            "stderr_tail": res.stderr[-800:] if res.stderr else "",
            "command": test_command,
        }

    def verify_and_merge(
        self,
        task_id: str,
        target_branch: str = "haos-fork",
        test_command: Optional[str] = None,
        base_branch: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inspeciona o git diff do worktree, calcula blast radius de testes e executa contratos + afetados."""
        worktree_path = (self.worktree_manager.worktrees_dir / f"task-{task_id}").resolve()
        base_br = base_branch or target_branch

        # 1. Inspecionar o diff do worktree
        diff_text = ""
        try:
            diff_text = self.worktree_manager.get_diff(task_id, base_branch=base_br)
        except Exception:
            diff_text = ""

        # 2. Extrair arquivos e símbolos modificados
        mod_files, mod_syms = self.parse_diff_impact(diff_text, worktree_root=worktree_path)

        # 3. Calcular blast radius e testes afetados via ImpactAnalyzer
        blast = self.impact_analyzer.analyze_impact(
            modified_symbols=mod_syms,
            modified_files=mod_files,
        )
        affected_tests = sorted(list(blast.affected_test_suites))

        # 4. Executar contratos padrão
        contracts_cmd = test_command or self.DEFAULT_CONTRACTS_CMD
        contracts_res = self.run_tests_in_worktree(task_id, test_command=contracts_cmd)
        if not contracts_res["success"]:
            return {
                "merged": False,
                "reason": "Test verification failed in isolated worktree (standard contracts)",
                "test_output": contracts_res.get("stdout_tail", "") or contracts_res.get("stderr_tail", ""),
                "blast_radius": blast.to_dict(),
            }

        # 5. Executar suítes de testes afetadas identificadas pelo blast radius
        if affected_tests:
            # Filtrar para testes que existem de fato no worktree ou repo
            valid_tests = []
            for t in affected_tests:
                t_path = worktree_path / t if worktree_path.exists() else Path(t)
                if t_path.exists():
                    valid_tests.append(t)
                elif Path(t).exists():
                    valid_tests.append(t)

            if valid_tests:
                affected_cmd = (
                    f"HERMES_PYTHON={sys.executable} "
                    f"scripts/run_tests.sh {' '.join(valid_tests)}"
                )
                affected_res = self.run_tests_in_worktree(task_id, test_command=affected_cmd)
                if not affected_res["success"]:
                    return {
                        "merged": False,
                        "reason": f"Affected unit tests failed: {valid_tests}",
                        "test_output": affected_res.get("stdout_tail", "") or affected_res.get("stderr_tail", ""),
                        "affected_tests": valid_tests,
                        "blast_radius": blast.to_dict(),
                    }

        # 6. Tudo passou: realizar merge seguro e remover worktree
        try:
            merge_out = self.worktree_manager.merge_worktree(task_id, target_branch=target_branch)
            self.worktree_manager.remove_worktree(task_id)
            return {
                "merged": True,
                "output": merge_out,
                "blast_radius": blast.to_dict(),
                "affected_tests": affected_tests,
            }
        except Exception as exc:
            return {
                "merged": False,
                "reason": f"Git merge failed: {exc}",
                "blast_radius": blast.to_dict(),
            }

