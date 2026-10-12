"""Tests for RE Evaluation Harness (Phase 6).
Validates that all 4 declarative JSON evaluation scenarios pass deterministically.
"""

from pathlib import Path
import pytest

from evals.re.runner import REEvalRunner


def test_eval_harness_all_scenarios_pass():
    runner = REEvalRunner()
    results = runner.run_all()

    assert len(results) == 4, f"Expected 4 evaluation scenarios, got {len(results)}"

    failures = []
    for res in results:
        if not res.passed:
            failures.append(f"{res.scenario_name}: {res.error or res.details}")

    assert not failures, f"Evaluation failures detected: {failures}"


def test_eval_harness_individual_scenarios():
    runner = REEvalRunner()
    
    # 1. Triage known binary
    triage_res = runner.run_scenario(runner.load_scenario("triage_known_binary"))
    assert triage_res.passed, f"Triage scenario failed: {triage_res.error}"
    assert triage_res.details["header_class"] == 64

    # 2. Backbone radare2
    r2_res = runner.run_scenario(runner.load_scenario("disassembly_r2"))
    assert r2_res.passed, f"R2 scenario failed: {r2_res.error}"
    assert r2_res.details["has_entry_or_main"] is True

    # 3. Ghidra decompile
    ghidra_res = runner.run_scenario(runner.load_scenario("decompile_ghidra"))
    assert ghidra_res.passed, f"Ghidra scenario failed: {ghidra_res.error}"
    assert ghidra_res.details["contains_c"] is True

    # 4. Permission denial
    perm_res = runner.run_scenario(runner.load_scenario("permission_denial"))
    assert perm_res.passed, f"Permission scenario failed: {perm_res.error}"
    assert perm_res.details["allowed"] is False
    assert perm_res.details["outcome"] == "deny"
    assert perm_res.details["target_sha256"] is not None
