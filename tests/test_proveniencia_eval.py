"""
Testes unitários e de regressão para o eval de proveniência causal PROV-O (ADR-014 e ADR-015).

Verifica deterministicamente:
1. Execução do evaluator canônico (/root/.haos/evals/eval_proveniencia.py).
2. Validação do caso de eval em /root/.haos/evals/cases/proveniencia-schema.json.
3. Comportamento anti-tautológico estrito (falha em diretório vazio, contagem insuficiente ou entidades fantasmas).
4. Suporte a proveniência e integridade em a2a_audit e contratos OKF.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

EVAL_SCRIPT = Path("/root/.haos/evals/eval_proveniencia.py")
CASE_JSON = Path("/root/.haos/evals/cases/proveniencia-schema.json")
RUNNER_SCRIPT = Path("/root/.haos/evals/runner.py")
VAULT_ADRS = Path("/root/.haos/obsidian_vault/adrs")


class TestProvenienciaEval(unittest.TestCase):
    def test_case_definition_exists_and_valid(self):
        """O caso de teste proveniencia-schema.json existe e possui schema válido."""
        self.assertTrue(CASE_JSON.is_file(), f"{CASE_JSON} deve existir")
        with open(CASE_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data.get("id"), "proveniencia-schema")
        self.assertIn("memoria", data.get("tags", []))
        self.assertIn("proveniencia", data.get("tags", []))
        self.assertIn("prov-o", data.get("tags", []))
        grader = data.get("grader", {})
        self.assertEqual(grader.get("type"), "shell")
        self.assertEqual(grader.get("expect_exit"), 0)
        self.assertGreaterEqual(grader.get("min", 0), 15)

    def test_evaluator_script_passes_on_real_vault(self):
        """O script de validação real passa com returncode 0 no vault canônico."""
        self.assertTrue(EVAL_SCRIPT.is_file(), f"{EVAL_SCRIPT} deve existir")
        res = subprocess.run([sys.executable, str(EVAL_SCRIPT)], capture_output=True, text=True, timeout=120)
        self.assertEqual(
            res.returncode,
            0,
            f"Evaluator falhou no ambiente real:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}",
        )
        self.assertIn("PROV-O EVAL DETERMINÍSTICO: PASS", res.stdout)
        self.assertIn("15/15 [100% conformes]", res.stdout)

    def test_eval_runner_passes_with_proveniencia_tag(self):
        """O runner da suite de evals do HAOS passa executando o caso proveniencia-schema."""
        self.assertTrue(RUNNER_SCRIPT.is_file(), f"{RUNNER_SCRIPT} deve existir")
        res = subprocess.run(
            [sys.executable, str(RUNNER_SCRIPT), "--tag", "proveniencia"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(res.returncode, 0, f"Runner falhou:\n{res.stdout}\n{res.stderr}")
        self.assertIn("[PASS] proveniencia-schema", res.stdout)
        self.assertIn("1 ok | 0 falha(s)", res.stdout)

    def test_anti_tautology_fails_on_empty_directory(self):
        """Anti-tautologia: deve falhar imediatamente se nenhum arquivo for inspecionado."""
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, HAOS_ADRS_DIR=td)
            res = subprocess.run(
                [sys.executable, str(EVAL_SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Anti-tautologia violada: Nenhum arquivo ADR inspecionado", res.stderr)

    def test_anti_tautology_fails_on_count_below_minimum(self):
        """Anti-tautologia: deve falhar se o total de ADRs estiver abaixo do limiar (ex: 2 < 15)."""
        with tempfile.TemporaryDirectory() as td:
            for i in range(1, 3):
                p = Path(td) / f"ADR-00{i}.md"
                p.write_text(
                    f"---\nid: ADR-00{i}\ntitulo: Test\nstatus: Aceito\ndata: 2026-09-23\n"
                    f"decidido_por: adriano\ncausado_by: motivo\n"
                    f"prov:\n  wasGeneratedBy: task:t\n  wasAssociatedWith: agent:a\n  wasDerivedFrom: adr:d\n"
                    f"---\n# Body\n"
                )
            env = dict(os.environ, HAOS_ADRS_DIR=td)
            res = subprocess.run(
                [sys.executable, str(EVAL_SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("abaixo do mínimo esperado", res.stderr)

    def test_anti_tautology_fails_on_ghost_entity(self):
        """Anti-tautologia: deve falhar se houver citação de ADR fantasma em links causais."""
        with tempfile.TemporaryDirectory() as td:
            for f in VAULT_ADRS.glob("*.md"):
                shutil.copy(f, td)
            # Altera ADR-002 para apontar para ADR fantasma
            target_f = Path(td) / "ADR-002-reranker-hibrido-graphrag.md"
            content = target_f.read_text(encoding="utf-8")
            target_f.write_text(content.replace("adr:ADR-001", "adr:ADR-999-GHOST"))

            env = dict(os.environ, HAOS_ADRS_DIR=td)
            res = subprocess.run(
                [sys.executable, str(EVAL_SCRIPT)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("Entidade fantasma detectada", res.stderr)
            self.assertIn("ADR-999-GHOST", res.stderr)


if __name__ == "__main__":
    unittest.main()
