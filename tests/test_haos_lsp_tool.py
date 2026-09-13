"""Contract tests for HAOS LSP Query Tool."""

import json
from pathlib import Path
import pytest

from tools.haos_lsp_tool import lsp_query_tool, _format_lsp_response


def test_lsp_tool_file_not_found(tmp_path: Path):
    res_raw = lsp_query_tool("definition", str(tmp_path / "nonexistent.py"), 10)
    res = json.loads(res_raw)
    assert res["success"] is False
    assert "File not found" in res["error"]


def test_format_lsp_response_hover():
    raw_hover = {
        "contents": {"value": "def calculate_total(a: int, b: int) -> int:\nReturns sum."}
    }
    formatted = _format_lsp_response("hover", raw_hover)
    assert formatted["success"] is True
    assert "calculate_total" in formatted["info"]


def test_format_lsp_response_definition():
    raw_def = [
        {
            "uri": "file:///tmp/repo/src/core.py",
            "range": {
                "start": {"line": 42, "character": 4},
                "end": {"line": 42, "character": 12},
            },
        }
    ]
    formatted = _format_lsp_response("definition", raw_def)
    assert formatted["success"] is True
    assert formatted["count"] == 1
    item = formatted["results"][0]
    assert item["line"] == 43
    assert item["character"] == 4
    assert item["path"] == "/tmp/repo/src/core.py"


def test_format_lsp_response_empty():
    formatted = _format_lsp_response("references", None)
    assert formatted["success"] is True
    assert formatted["results"] == []
