"""Tests for MCP result budget and spill-to-file mechanism."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import pytest

from hermes.platform.mcp.spill import (
    apply_mcp_result_budget,
    get_mcp_result_budget,
    DEFAULT_MCP_RESULT_BUDGET,
)
from tools.mcp_tool_content import _truncate_mcp_text_result


class TestMCPResultBudget:
    def test_small_result_passes_intact(self):
        with TemporaryDirectory() as tmpdir:
            spills_dir = Path(tmpdir)
            small_text = "Hello world, this is a normal MCP output."
            res = apply_mcp_result_budget(small_text, tool_name="echo", budget=1000, spills_dir=spills_dir)
            assert res == small_text
            # No files spilled
            assert len(list(spills_dir.iterdir())) == 0

    def test_large_result_is_truncated_and_spilled(self):
        with TemporaryDirectory() as tmpdir:
            spills_dir = Path(tmpdir)
            # Create a large output of 1500 chars with budget 500
            large_text = "ABCDEFGHIJ" * 150  # 1500 chars
            budget = 500

            res = apply_mcp_result_budget(large_text, tool_name="fetch_data", budget=budget, spills_dir=spills_dir)

            # Check that return starts with the truncated prefix of exact budget size
            assert res.startswith(large_text[:budget])
            # Check reference to file exists in return
            assert "[truncado; completo em " in res

            # Check spill file on disk
            spill_files = list(spills_dir.glob("*-fetch_data.txt"))
            assert len(spill_files) == 1
            spill_file = spill_files[0]
            assert str(spill_file.resolve()) in res

            # Verify the spill file contains the full intact content
            spilled_content = spill_file.read_text(encoding="utf-8")
            assert spilled_content == large_text

    def test_env_var_budget_configuration(self, monkeypatch):
        with TemporaryDirectory() as tmpdir:
            spills_dir = Path(tmpdir)
            monkeypatch.setenv("HAOS_MCP_RESULT_BUDGET", "300")
            assert get_mcp_result_budget() == 300

            payload = "x" * 500
            res = apply_mcp_result_budget(payload, tool_name="tool_env", spills_dir=spills_dir)

            assert res.startswith("x" * 300)
            assert "[truncado; completo em " in res
            assert len(list(spills_dir.iterdir())) == 1

    def test_truncate_mcp_text_result_integration(self, monkeypatch):
        with TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("HERMES_HOME", tmpdir)
            monkeypatch.setenv("HAOS_MCP_RESULT_BUDGET", "250")

            long_text = "Z" * 1000
            res = _truncate_mcp_text_result(long_text, tool_name="test_tool")

            assert "[truncado; completo em " in res
            spills_dir = Path(tmpdir) / "mcp-spills"
            assert spills_dir.exists()
            assert len(list(spills_dir.iterdir())) == 1
