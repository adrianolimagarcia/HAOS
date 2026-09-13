"""Contract tests for HAOS DSH Delegation Tool (dsh_run)."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from tools.haos_dsh_tool import dsh_run_tool, _check_dsh_available


def test_dsh_run_empty_objective():
    res = json.loads(dsh_run_tool(""))
    assert res["success"] is False
    assert "Objective cannot be empty" in res["error"]


def test_dsh_run_invalid_workdir():
    res = json.loads(dsh_run_tool("test", workdir="/path/to/nonexistent/directory/xyz"))
    assert res["success"] is False
    assert "Target workdir does not exist" in res["error"]


def test_dsh_run_successful_mock(tmp_path: Path):
    mock_res = MagicMock()
    mock_res.returncode = 0
    mock_res.stdout = "DSH execution completed successfully with 3 files modified."

    with patch("subprocess.run", return_value=mock_res) as mock_run:
        res_raw = dsh_run_tool("Refatorar login", workdir=str(tmp_path))
        res = json.loads(res_raw)
        assert res["success"] is True
        assert res["exit_code"] == 0
        assert "DSH execution completed" in res["output"]
        assert mock_run.called
        called_args = mock_run.call_args[0][0]
        assert "exec" in called_args
        assert "--objective" in called_args
        assert "Refatorar login" in called_args


def test_dsh_availability_check():
    with patch("shutil.which", return_value="/usr/local/bin/dsh"):
        assert _check_dsh_available() is True

    with patch("shutil.which", return_value=None), patch.dict("os.environ", {}, clear=True):
        assert _check_dsh_available() is False
