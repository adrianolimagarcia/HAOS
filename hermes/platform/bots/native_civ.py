"""Native C-ABI Bridge to Rust haos-civ crate.

Provides SIMD/compiled acceleration for identity hashing, leaf SOUL derivation,
and event integrity validation with transparent fallback to pure Python.
"""

from __future__ import annotations

import ctypes
import json
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_LIB: Optional[ctypes.CDLL] = None
_INIT_ATTEMPTED = False


def _find_libhaos_civ() -> Optional[Path]:
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [
        repo_root / "target" / "release" / "libhaos_civ.so",
        repo_root / "target" / "debug" / "libhaos_civ.so",
        repo_root / "packages" / "haos-civ" / "target" / "release" / "libhaos_civ.so",
        repo_root / "packages" / "haos-civ" / "target" / "debug" / "libhaos_civ.so",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def get_native_lib() -> Optional[ctypes.CDLL]:
    global _LIB, _INIT_ATTEMPTED
    if _INIT_ATTEMPTED:
        return _LIB
    _INIT_ATTEMPTED = True

    lib_path = _find_libhaos_civ()
    if not lib_path:
        return None

    try:
        lib = ctypes.CDLL(str(lib_path))
        lib.haos_civ_sha256.argtypes = [ctypes.c_char_p]
        lib.haos_civ_sha256.restype = ctypes.c_void_p

        lib.haos_civ_bundle_hash.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        lib.haos_civ_bundle_hash.restype = ctypes.c_void_p

        lib.haos_civ_temporary_soul.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        lib.haos_civ_temporary_soul.restype = ctypes.c_void_p

        lib.haos_civ_free_string.argtypes = [ctypes.c_void_p]
        lib.haos_civ_free_string.restype = None

        _LIB = lib
        logger.info("Loaded native haos-civ Rust library: %s", lib_path)
    except Exception as exc:
        logger.debug("Failed loading native haos-civ library: %s", exc)
        _LIB = None

    return _LIB


def is_native_available() -> bool:
    return get_native_lib() is not None


def native_compute_sha256(text: str) -> Optional[str]:
    lib = get_native_lib()
    if not lib:
        return None
    try:
        ptr = lib.haos_civ_sha256(text.encode("utf-8"))
        if not ptr:
            return None
        res = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")
        lib.haos_civ_free_string(ptr)
        return res
    except Exception:
        return None


def native_compute_bundle_hash(soul: str, identity: str, values: str) -> Optional[str]:
    lib = get_native_lib()
    if not lib:
        return None
    try:
        ptr = lib.haos_civ_bundle_hash(
            soul.encode("utf-8"),
            identity.encode("utf-8"),
            values.encode("utf-8"),
        )
        if not ptr:
            return None
        res = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")
        lib.haos_civ_free_string(ptr)
        return res
    except Exception:
        return None


def native_build_temporary_soul(
    parent_soul: str,
    task_description: str,
    constraints: List[str],
    council_context: Optional[str] = None,
) -> Optional[str]:
    lib = get_native_lib()
    if not lib:
        return None
    try:
        c_json = json.dumps(constraints)
        ptr = lib.haos_civ_temporary_soul(
            parent_soul.encode("utf-8"),
            task_description.encode("utf-8"),
            c_json.encode("utf-8"),
            (council_context.encode("utf-8") if council_context else None),
        )
        if not ptr:
            return None
        res = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8")
        lib.haos_civ_free_string(ptr)
        return res
    except Exception:
        return None
