#!/usr/bin/env python3
"""Compatibility launcher for the PROV-O CLI.

The legacy implementation lives in ``adr_prov_legacy.py``. Rust is selected
only by an explicit ``HAOS_PROV_BIN`` executable; otherwise this launcher runs
the legacy implementation. The self-path is rejected to prevent recursion.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys

_LEGACY_PATH = Path(__file__).with_name("adr_prov_legacy.py")
_SPEC = importlib.util.spec_from_file_location("adr_prov_legacy", _LEGACY_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover
    raise ImportError(f"cannot load legacy PROV implementation: {_LEGACY_PATH}")
_LEGACY = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _LEGACY
_SPEC.loader.exec_module(_LEGACY)

# Preserve the historical import surface used by in-workspace oracle tools.
for _name, _value in vars(_LEGACY).items():
    if _name not in {"__name__", "__loader__", "__package__", "__spec__"}:
        globals()[_name] = _value


def _configured_rust_binary() -> Path | None:
    raw = os.environ.get("HAOS_PROV_BIN", "").strip()
    if not raw:
        return None
    candidate = Path(shutil.which(raw) or raw)
    try:
        resolved = candidate.resolve()
        if resolved == Path(__file__).resolve():
            return None
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    except OSError:
        pass
    return None


def _exec_rust_or_none(argv: list[str]) -> bool:
    binary = _configured_rust_binary()
    if binary is None:
        return False
    try:
        # exec preserves the legacy process boundary, streams, and exit code.
        os.execv(str(binary), [str(binary), *argv])
    except OSError:
        # Optional accelerator failures fall back deterministically.
        return False
    return True  # pragma: no cover


def main() -> None:
    if _exec_rust_or_none(sys.argv[1:]):
        return
    _LEGACY.main()


if __name__ == "__main__":
    main()
