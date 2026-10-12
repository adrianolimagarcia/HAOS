"""Tests for Radare2 Backbone via MCP (Phase 2).
Validates r2mcp integration, binary opening, function enumeration, and budget spillover.
"""

import os
from pathlib import Path
import pytest

from hermes.platform.re.r2_backbone import Radare2MCPClient
from hermes_constants import get_hermes_home


@pytest.fixture
def r2_client():
    client = Radare2MCPClient()
    if not client.is_available():
        pytest.skip("r2mcp executable not available on host")
    with client:
        yield client


def test_r2_client_availability():
    client = Radare2MCPClient()
    assert client.is_available(), "r2mcp should be installed and executable"


def test_r2_open_and_list_functions(r2_client):
    target = "/bin/ls"
    assert os.path.exists(target), f"Test binary not found: {target}"

    open_res = r2_client.open_file(target)
    assert "File opened" in open_res or "successfully" in open_res.lower()

    anal_res = r2_client.analyze()
    assert "Analysis completed" in anal_res or "functions" in anal_res.lower()

    functions_out = r2_client.list_functions()
    assert len(functions_out) > 0
    # Must discover entrypoint or functions
    assert "entry0" in functions_out or "main" in functions_out or "fcn." in functions_out


def test_r2_budget_spillover_on_large_output(r2_client):
    target = "/bin/ls"
    r2_client.open_file(target)
    r2_client.analyze()

    # Enforce an artificially low budget (e.g. 200 chars) to verify spill-to-file behavior
    low_budget = 200
    spilled = r2_client.list_functions(budget=low_budget)

    assert len(spilled) > 0
    # When output exceeds budget, spill marker should be attached
    assert "[truncado; completo em " in spilled
    assert spilled.endswith("]")

    # Extract spill file path from marker
    marker_start = spilled.index("[truncado; completo em ") + len("[truncado; completo em ")
    marker_end = spilled.rindex("]")
    spill_file_path = spilled[marker_start:marker_end].strip()

    assert os.path.isfile(spill_file_path), f"Spill file should exist on disk at {spill_file_path}"
    content = Path(spill_file_path).read_text(encoding="utf-8")
    assert len(content) > low_budget
    assert "entry0" in content or "fcn." in content
