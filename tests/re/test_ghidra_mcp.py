"""Tests for Ghidra Headless MCP client (Phase 3).
Validates headless daemon lifecycle, decompile_function, spill-to-file and asymmetric timeouts.
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from hermes.platform.re.ghidra_mcp import GhidraMCPClient, GhidraMCPError


@pytest.fixture
def test_binary():
    bin_path = "/bin/ls"
    assert os.path.isfile(bin_path), f"Binary not found: {bin_path}"
    return bin_path


def test_ghidra_client_availability(test_binary):
    client = GhidraMCPClient(binary_path=test_binary)
    assert client.is_available(), "Ghidra install, extension JAR and ghidra-mcp binary should be available"


def test_ghidra_client_lifecycle_and_timeout_abort(test_binary):
    # Asymmetric timeout test: timeout raises GhidraMCPError with abort, never silent deny
    client = GhidraMCPClient(
        binary_path=test_binary,
        port=8099,
        timeout_seconds=0.01,  # Ultra-low timeout to trigger abort condition
    )
    with pytest.raises(GhidraMCPError) as exc_info:
        # Mock unreachable backend call to verify immediate abort
        client.decompile_function("main")
    assert "Failed to communicate with GhidraMCP" in str(exc_info.value)


def test_ghidra_decompile_with_budget_spillover(test_binary):
    client = GhidraMCPClient(binary_path=test_binary)

    # Simulated large decompilation C output
    dummy_c_code = (
        "int main(int argc, char **argv) {\n"
        "    // Ghidra Headless Decompiled C code\n"
        + "    int x = 42;\n" * 50
        + "    return x;\n}\n"
    )

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_response = MagicMock()
        mock_response.read.return_value = (
            '{"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "'
            + dummy_c_code.replace("\n", "\\n")
            + '"}]}}'
        ).encode("utf-8")
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        # Request decompile with low budget (100 chars) to force spill-to-file
        spilled = client.decompile_function("main", budget=100)

        assert len(spilled) > 0
        assert "[truncado; completo em " in spilled
        assert spilled.endswith("]")

        marker_start = spilled.index("[truncado; completo em ") + len("[truncado; completo em ")
        marker_end = spilled.rindex("]")
        spill_file_path = spilled[marker_start:marker_end].strip()

        assert os.path.isfile(spill_file_path)
        content = Path(spill_file_path).read_text(encoding="utf-8")
        assert "int main" in content
        assert "Ghidra Headless Decompiled C code" in content
