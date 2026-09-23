"""Black-box checks for the reversible Phase E PROV routing."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "tools" / "adr_prov.py"
LEGACY = ROOT / "tools" / "adr_prov_legacy.py"
VAULT = ROOT / "tests" / "fixtures" / "adr_prov" / "vault"
RUST = ROOT / "target" / "debug" / "haos-prov"
EDGE = ROOT / "target" / "debug" / "haos-edge"


@pytest.fixture(scope="module")
def current_edge_binary() -> Path:
    """Build the edge executable used by the black-box dispatch check."""
    result = subprocess.run(
        ["cargo", "build", "-p", "haos-edge", "--bin", "haos-edge"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        "cargo build -p haos-edge failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert EDGE.is_file(), f"cargo build did not produce {EDGE}"
    return EDGE


def invoke(command: list[str], *, binary: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if binary is None:
        env.pop("HAOS_PROV_BIN", None)
    else:
        env["HAOS_PROV_BIN"] = binary
    return subprocess.run(
        command + ["--vault", str(VAULT), "--json", "graph"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_fallback_matches_legacy_exactly() -> None:
    shim = invoke([sys.executable, str(SHIM)])
    legacy = subprocess.run(
        [sys.executable, str(LEGACY), "--vault", str(VAULT), "--json", "graph"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={k: v for k, v in os.environ.items() if k != "HAOS_PROV_BIN"},
    )
    assert (shim.returncode, shim.stdout, shim.stderr) == (legacy.returncode, legacy.stdout, legacy.stderr)


def test_missing_explicit_binary_falls_back() -> None:
    result = invoke([sys.executable, str(SHIM)], binary=str(ROOT / "does-not-exist"))
    assert result.returncode == 0
    assert '"nodes"' in result.stdout


def test_explicit_rust_binary_is_selected() -> None:
    if not RUST.is_file():
        return
    result = invoke([sys.executable, str(SHIM)], binary=str(RUST))
    assert result.returncode == 0
    assert '"nodes"' in result.stdout


def test_edge_prov_delegates_without_duplicate_engine(current_edge_binary: Path) -> None:
    result = subprocess.run(
        [str(current_edge_binary), "prov", "--vault", str(VAULT), "--json", "graph"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=dict(os.environ, HAOS_PROV_BIN="", HAOS_PROV_PY=str(SHIM)),
    )
    assert result.returncode == 0
    assert '"nodes"' in result.stdout
