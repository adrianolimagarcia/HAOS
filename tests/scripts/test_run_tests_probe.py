import os
import subprocess
from pathlib import Path
import pytest

def test_run_tests_syntax():
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_tests.sh"
    res = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert res.returncode == 0

def test_run_tests_probe_failure_hints(tmp_path):
    """When no venv exists, run_tests.sh should exit with code 1 and mention HERMES_PYTHON and probed paths."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_tests.sh"
    # Run in an empty temporary directory with HOME pointing to empty dir and no git repo
    empty_home = tmp_path / "home"
    empty_home.mkdir()
    empty_work = tmp_path / "work"
    empty_work.mkdir()

    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(empty_home),
    }
    # unset HERMES_PYTHON and VIRTUAL_ENV
    res = subprocess.run(
        ["bash", str(script), "dummy_test.py"],
        cwd=str(empty_work),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    assert "HERMES_PYTHON" in res.stderr
    assert "no virtualenv with pytest found" in res.stderr
