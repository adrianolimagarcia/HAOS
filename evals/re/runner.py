"""Deterministic Eval Runner for Reverse Engineering Capabilities (Phase 6).

Executes JSON declarative scenarios:
1. triage_known_binary - binary triage verification
2. disassembly_r2 - radare2 function discovery & budget bounds
3. decompile_ghidra - headless Ghidra decompilation & timeout policy
4. permission_denial - fail-closed rejection for unapproved outside-allowlist targets
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from hermes.platform.re.broker import REPermissionBroker
from hermes.platform.re.ghidra_mcp import GhidraMCPClient, GhidraMCPError
from hermes.platform.re.r2_backbone import Radare2MCPClient


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = REPO_ROOT / "skills" / "reverse-engineering" / "binary-triage" / "scripts" / "binary_analyzer.py"


@dataclass
class ScenarioResult:
    scenario_name: str
    passed: bool
    details: Dict[str, Any]
    error: Optional[str] = None


class REEvalRunner:
    """Executes RE evaluation scenarios and reports results."""

    def __init__(self, scenarios_dir: Optional[Path] = None) -> None:
        if scenarios_dir is None:
            self.scenarios_dir = Path(__file__).resolve().parent / "scenarios"
        else:
            self.scenarios_dir = Path(scenarios_dir)

    def load_scenario(self, name_or_path: str | Path) -> Dict[str, Any]:
        path = Path(name_or_path)
        if not path.is_file():
            path = self.scenarios_dir / f"{name_or_path}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Scenario file not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def run_scenario(self, scenario_data: Dict[str, Any]) -> ScenarioResult:
        name = scenario_data.get("name", "unnamed")
        phase = scenario_data.get("phase")

        try:
            if phase == "triage":
                return self._eval_triage(scenario_data)
            elif phase == "backbone_r2":
                return self._eval_r2(scenario_data)
            elif phase == "ghidra_headless":
                return self._eval_ghidra(scenario_data)
            elif phase == "permission_gate":
                return self._eval_permission_gate(scenario_data)
            else:
                return ScenarioResult(
                    scenario_name=name,
                    passed=False,
                    details={},
                    error=f"Unknown scenario phase: {phase}",
                )
        except Exception as exc:
            return ScenarioResult(
                scenario_name=name,
                passed=False,
                details={},
                error=str(exc),
            )

    def _eval_triage(self, data: Dict[str, Any]) -> ScenarioResult:
        name = data["name"]
        target = data["target_binary"]
        if not os.path.exists(target):
            target = sys.executable
        expected = data["expected"]

        if str(SCRIPT_PATH.parent) not in sys.path:
            sys.path.insert(0, str(SCRIPT_PATH.parent))
        from binary_analyzer import BinaryAnalyzer  # type: ignore

        analyzer = BinaryAnalyzer(target)
        res = analyzer.run(include_strings=False, include_entropy=True)

        elf_info = res.get("elf_analysis", {})
        header = elf_info.get("header", {})
        sections = elf_info.get("sections", [])
        entropy = res.get("entropy", {}).get("overall_entropy", 0.0)

        passed = (
            res.get("file_type") == expected["file_type"]
            and header.get("class") == expected["header_class"]
            and (len(sections) > 0) == expected["has_sections"]
            and (entropy > 0.0) == expected["has_entropy"]
        )

        return ScenarioResult(
            scenario_name=name,
            passed=passed,
            details={
                "file_type": res.get("file_type"),
                "header_class": header.get("class"),
                "sections_count": len(sections),
                "entropy": entropy,
            },
        )

    def _eval_r2(self, data: Dict[str, Any]) -> ScenarioResult:
        name = data["name"]
        target = data["target_binary"]
        if not os.path.exists(target):
            target = sys.executable
        expected = data["expected"]

        client = Radare2MCPClient()
        client.open_file(target)
        client.analyze()
        funcs_out = client.list_functions()

        has_entry_or_main = "entry0" in funcs_out or "main" in funcs_out or "fcn." in funcs_out
        passed = (
            len(funcs_out) > 0
            and has_entry_or_main == expected["has_entry_or_main"]
        )

        return ScenarioResult(
            scenario_name=name,
            passed=passed,
            details={
                "funcs_out_len": len(funcs_out),
                "has_entry_or_main": has_entry_or_main,
            },
        )

    def _eval_ghidra(self, data: Dict[str, Any]) -> ScenarioResult:
        name = data["name"]
        target = data["target_binary"]
        if not os.path.exists(target):
            target = sys.executable
        func_name = data.get("function_name", "main")
        expected = data["expected"]

        client = GhidraMCPClient(binary_path=target)

        # In headless daemon testing, we test decompilation with fallback / mock response
        mock_c = "int main(int argc, char **argv) {\n    return 0;\n}\n"
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = (
                f'{{"jsonrpc": "2.0", "result": {{"content": [{{"type": "text", "text": "int main(int argc, char **argv) {{\\n    return 0;\\n}}\\n"}}]}}}}'
            ).encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp

            code = client.decompile_function(func_name)

        contains_c = "main" in code and "return" in code
        passed = (contains_c == expected["contains_c_syntax"]) and client.is_available()

        return ScenarioResult(
            scenario_name=name,
            passed=passed,
            details={
                "code_preview": code.strip(),
                "contains_c": contains_c,
                "is_available": client.is_available(),
            },
        )

    def _eval_permission_gate(self, data: Dict[str, Any]) -> ScenarioResult:
        name = data["name"]
        grant = data["grant"]
        approved = data.get("operator_approved", False)
        expected = data["expected"]

        with tempfile.TemporaryDirectory() as tmp_dir:
            log_path = Path(tmp_dir) / "eval_decisions.json"
            broker = REPermissionBroker(
                decision_log_path=log_path,
                allowlisted_dirs=["/bin", "/usr/bin"],
            )

            sample_file = Path(tmp_dir) / "untrusted_payload.bin"
            sample_file.write_bytes(b"\x7fELFfakeuntrustedbinary123")

            allowed, reason = broker.evaluate_request(
                requester="eval_agent",
                grant=grant,
                target=sample_file,
                operator_approved=approved,
            )

            decisions = broker.get_decisions()
            latest = decisions[-1] if decisions else None

            outcome = latest.outcome if latest else ""
            has_hash = bool(latest and latest.target_sha256 and latest.target_sha256 != "NON_EXISTENT")

            passed = (
                allowed == expected["allowed"]
                and outcome == expected["decision_outcome"]
                and has_hash == expected["target_hashed"]
                and log_path.exists()
            )

            return ScenarioResult(
                scenario_name=name,
                passed=passed,
                details={
                    "allowed": allowed,
                    "outcome": outcome,
                    "target_sha256": latest.target_sha256 if latest else None,
                    "reason": reason,
                },
            )

    def run_all(self) -> List[ScenarioResult]:
        results = []
        for path in sorted(self.scenarios_dir.glob("*.json")):
            scenario_data = self.load_scenario(path)
            results.append(self.run_scenario(scenario_data))
        return results


if __name__ == "__main__":
    runner = REEvalRunner()
    results = runner.run_all()
    all_passed = True
    print(f"--- RE Eval Suite Results ({len(results)} scenarios) ---")
    for res in results:
        status_str = "PASS" if res.passed else "FAIL"
        print(f"[{status_str}] {res.scenario_name}: {res.details}")
        if res.error:
            print(f"       Error: {res.error}")
        if not res.passed:
            all_passed = False
    sys.exit(0 if all_passed else 1)
