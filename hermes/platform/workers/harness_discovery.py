"""HAOS Harness Auto-Discovery: filesystem search & PATH installation.

Whenever a requested execution harness is not declared on PATH / env vars, HAOS
probes the local filesystem (bounded walk of home, project roots and
HAOS_HARNESS_SEARCH_DIRS) for a known install of that tool. When found, a
launcher is installed into ~/.haos/bin (already part of PATH) so the harness
becomes available for the rest of the session and future ones. When nothing is
found, the caller receives a ``missing`` HarnessInfo whose ``error`` carries
clear guidance and the dispatcher falls back to the native local worker.

Discovery is intentionally bounded and memoized (per-process success cache +
short failure TTL) so repeated detect() calls never rescan the disk.
"""

from __future__ import annotations

import logging
import os
import stat
import time
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover - import-time cycle guard
    from hermes.platform.workers.harness_registry import HarnessInfo

logger = logging.getLogger("hermes.platform.workers.harness_discovery")

# Executable file names searched inside candidate directories.
_TOOL_BIN_NAMES: Dict[str, List[str]] = {
    "dsh": ["dsh"],
    "opencode": ["opencode"],
    "agy": ["agy"],
    "codex": ["codex"],
    "claude-code": ["claude"],
    "acp": ["hermes-acp", "acp"],
}

# Well-known repository/install directories that hold a harness even when the
# launcher is not an executable (DSH source tree, AGY proxy).
_REPO_DIR_NAMES: Dict[str, List[str]] = {
    "dsh": ["deepseek-harness"],
    "agy": ["wrapper-antigravity", "antigravity", "agy"],
}

_HAOS_BIN = Path.home() / ".haos" / "bin"
_DEPTH_CAP = 5
_SKIP_DIRS = {
    ".git", ".cache", ".npm", ".cargo", ".rustup", ".venv", "venv", "__pycache__",
    ".android", ".local", "node_modules", ".hermes", ".ouroboros", ".nv", ".config",
}
# node_modules is skipped wholesale except for its .bin stubs, handled separately.
_FAILURE_TTL_SEC = 300.0  # re-scan at most once every 5 min when nothing found


def _search_roots() -> List[Path]:
    """Candidate roots for bounded discovery (env override first)."""
    roots = _env_search_dirs()

    home = Path.home()
    for sub in ("", ".local/bin", ".npm-global/bin", ".local/share", ".config", ".gemini",
                ".haos/bin", ".opencode", ".codex", ".claude"):
        p = (home / sub if sub else home).resolve()
        if p.is_dir() and p not in roots:
            roots.append(p)

    # Current working directory + up to 2 ancestors (covers project roots like
    # dsh-projetos/TEMP/references/<tool> when the session cwd sits inside them).
    # Ancestors are only *probed* with known candidate paths, never deep-walked,
    # so a project under /run/media or /tmp never triggers a filesystem-wide scan.
    try:
        cwd = Path.cwd().resolve()
        for anc in [cwd, *cwd.parents[:2]]:
            if anc == anc.parent:  # filesystem root "/"
                continue
            if anc.is_dir() and anc not in roots:
                roots.append(anc)
    except Exception:
        pass

    return roots


def _haos_bin_dir() -> Path:
    """~/.haos/bin, created on demand; it is already part of the HAOS PATH."""
    try:
        _HAOS_BIN.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - read-only home fallback
        logger.warning("Could not create %s: %s", _HAOS_BIN, exc)
    return _HAOS_BIN


def _ensure_on_path(bin_dir: Path) -> None:
    """Prepend bin_dir to os.environ PATH for the current process."""
    current = os.environ.get("PATH", "")
    parts = [p for p in current.split(os.pathsep) if p]
    target = str(bin_dir)
    if target not in parts:
        parts.insert(0, target)
        os.environ["PATH"] = os.pathsep.join(parts)
        logger.info("Prepending %s to PATH (harness auto-discovery)", target)


def _install_binary_launcher(name: str, found: Path) -> Optional[str]:
    """Symlink an existing executable into ~/.haos/bin; returns the launcher path."""
    bin_dir = _haos_bin_dir()
    link = bin_dir / name
    try:
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(str(found))
        _ensure_on_path(bin_dir)
        logger.info("Auto-discovered '%s' at %s -> installed %s", name, found, link)
        return str(link)
    except Exception as exc:
        logger.warning("Could not symlink '%s' from %s into %s: %s", name, found, bin_dir, exc)
        # Even without the link, PATH can point at the found binary directly.
        _ensure_on_path(found.parent)
        return str(found)


def _write_launcher(name: str, content: str) -> Optional[str]:
    """Write an executable launcher script into ~/.haos/bin; returns its path."""
    bin_dir = _haos_bin_dir()
    launcher = bin_dir / name
    try:
        launcher.write_text(content, encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        _ensure_on_path(bin_dir)
        logger.info("Installed auto-discovered launcher for '%s' at %s", name, launcher)
        return str(launcher)
    except Exception as exc:
        logger.warning("Could not write launcher for '%s' at %s: %s", name, launcher, exc)
        return None


def _dsh_launcher_from_repo(repo: Path) -> Optional[str]:
    """Launcher for the DeepSeek Harness source checkout (tsx source or built js)."""
    built_js = repo / "apps" / "cli" / "lib" / "bin.js"
    if built_js.is_file():
        return _write_launcher(
            "dsh",
            "#!/usr/bin/env bash\n"
            f'exec node "{built_js}" "$@"\n',
        )
    src_ts = repo / "apps" / "cli" / "src" / "bin.ts"
    if src_ts.is_file() and (repo / "node_modules").is_dir():
        return _write_launcher(
            "dsh",
            "#!/usr/bin/env bash\n"
            f'cd "{repo}"\n'
            f'exec pnpm exec tsx "apps/cli/src/bin.ts" "$@"\n',
        )
    return None


_AGY_CLIENT_TEMPLATE = """#!/usr/bin/env python3
""" + '"""HAOS auto-discovered AGY client (wrapper-antigravity proxy)."""' + """
import argparse, json, os, sys, urllib.request

def _chat(proxy, model, message):
    body = {
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "stream": False,
    }
    req = urllib.request.Request(
        proxy.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=int(os.environ.get("AGY_TIMEOUT", "600"))) as resp:
        data = json.loads(resp.read().decode())
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sub", nargs="?", default="chat", help="subcommand (chat)")
    ap.add_argument("--message", "-m", required=True)
    ap.add_argument("--cwd", default=None)
    args = ap.parse_args()
    proxy = os.environ.get("ANTIGRAVITY_PROXY_ENDPOINT", "http://127.0.0.1:8790")
    model = os.environ.get("ANTIGRAVITY_MODEL", "gemini-3.7-flash")
    sys.stdout.write(_chat(proxy, model, args.message) + "\\n")

if __name__ == "__main__":
    main()
"""


def _agy_launcher(endpoint: str) -> Optional[str]:
    """Client launcher that reaches the Antigravity proxy (OpenAI-compatible)."""
    return _write_launcher("agy", _AGY_CLIENT_TEMPLATE)


def _scan_root_for_binary(names: List[str], root: Path, depth: int = 0) -> Optional[Path]:
    """Bounded walk looking for an executable named like the tool."""
    if depth > _DEPTH_CAP:
        return None
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return None

    for entry in entries:
        if entry.is_symlink() or entry.is_file():
            if entry.name in names and os.access(entry, os.X_OK):
                return entry
    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name in _SKIP_DIRS:
            continue
        if entry.name == "node_modules":
            # Only the .bin stub dir can hold a launcher; check it and stop.
            bin_stub = entry / ".bin"
            if bin_stub.is_dir():
                for stub in bin_stub.iterdir():
                    if stub.name in names and os.access(stub, os.X_OK):
                        return stub
            continue
        hit = _scan_root_for_binary(names, entry, depth + 1)
        if hit is not None:
            return hit
    return None


def _find_repo_dir(names: List[str], root: Path, depth: int = 0) -> Optional[Path]:
    """Bounded walk looking for a well-known repo directory (e.g. deepseek-harness)."""
    if depth > _DEPTH_CAP:
        return None
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return None
    for entry in entries:
        if entry.is_dir() and entry.name in _SKIP_DIRS:
            continue
        if entry.is_dir() and entry.name in names:
            return entry
    for entry in entries:
        if not entry.is_dir() or entry.name in _SKIP_DIRS or entry.name == "node_modules":
            continue
        hit = _find_repo_dir(names, entry, depth + 1)
        if hit is not None:
            return hit
    return None


def _env_search_dirs() -> List[Path]:
    """Roots explicitly declared via HAOS_HARNESS_SEARCH_DIRS (deep-walkable)."""
    roots: List[Path] = []
    env_roots = os.environ.get("HAOS_HARNESS_SEARCH_DIRS", "")
    if env_roots:
        for raw in env_roots.split(os.pathsep):
            p = Path(raw).expanduser()
            if p.is_dir():
                roots.append(p.resolve())
    return roots


def _probe_binary_candidates(names: List[str], root: Path) -> List[Path]:
    """Direct candidate paths for an executable (no directory walking)."""
    candidates: List[Path] = []
    for name in names:
        candidates.append(root / name)
        candidates.append(root / "bin" / name)
        candidates.append(root / ".bin" / name)
        candidates.append(root / "node_modules" / ".bin" / name)
        candidates.append(root / ".local" / "bin" / name)
        candidates.append(root / ".npm-global" / "bin" / name)
    hits: List[Path] = []
    for cand in candidates:
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                hits.append(cand)
        except OSError:
            continue
    return hits


def _probe_repo_candidates(names: List[str], root: Path) -> List[Path]:
    """Direct candidate paths for well-known repo/install directories."""
    candidates: List[Path] = []
    for name in names:
        candidates.append(root / name)
        candidates.append(root / "TEMP" / "references" / name)
        candidates.append(root / "references" / name)
        candidates.append(root / "repos" / name)
        candidates.append(root / "vendor" / name)
    hits: List[Path] = []
    for cand in candidates:
        try:
            if cand.is_dir():
                hits.append(cand)
        except OSError:
            continue
    return hits


class HarnessAutoDiscovery:
    """Filesystem discovery + PATH installation, memoized per process."""

    def __init__(self) -> None:
        self._installed: Dict[str, str] = {}
        self._last_failure: Dict[str, float] = {}

    def discover(self, harness_name: str) -> Optional[HarnessInfo]:
        """Attempt to find and install a missing harness; None if truly absent."""
        name = harness_name.strip().lower()
        if name in self._installed:
            return HarnessInfo(
                name=name,
                available=True,
                status="available",
                executable=self._installed[name],
                description=f"{name} (auto-discovered)",
                details={"auto_discovered": True},
            )

        now = time.monotonic()
        last_fail = self._last_failure.get(name, 0.0)
        if now - last_fail < _FAILURE_TTL_SEC:
            return None

        info = self._search_and_install(name)
        if info is not None and info.available and info.executable:
            self._installed[name] = info.executable
            return info

        self._last_failure[name] = time.monotonic()
        return info  # available=False with guidance, or None

    def _search_and_install(self, name: str) -> Optional[HarnessInfo]:
        from hermes.platform.workers.harness_registry import HarnessInfo

        bin_names = _TOOL_BIN_NAMES.get(name, [name])
        repo_names = _REPO_DIR_NAMES.get(name, [])
        roots = _search_roots()
        env_roots = _env_search_dirs()
        found_binary: Optional[Path] = None
        found_repo: Optional[Path] = None

        # 1) Cheap candidate probing on every root (never deep-walks home or
        #    ancestor trees). Finds: <root>/bin/<tool>, <root>/<tool>,
        #    node_modules/.bin stubs, and known repo layouts.
        for root in roots:
            for hit in _probe_binary_candidates(bin_names, root):
                found_binary = hit
                break
            if found_binary is not None:
                break
            if repo_names:
                for hit in _probe_repo_candidates(repo_names, root):
                    found_repo = hit
                    break
                if found_repo is not None:
                    break

        # 2) Bounded deep walk ONLY on explicitly declared search dirs
        #    (HAOS_HARNESS_SEARCH_DIRS) — the user pointed HAOS at those trees.
        if found_binary is None or found_repo is None:
            for root in env_roots:
                if found_binary is None:
                    found_binary = _scan_root_for_binary(bin_names, root)
                if found_repo is None and repo_names:
                    found_repo = _find_repo_dir(repo_names, root)
                if found_binary is not None:
                    break

        launcher: Optional[str] = None

        if name == "dsh" and found_repo is not None:
            launcher = _dsh_launcher_from_repo(found_repo)
            if launcher:
                return HarnessInfo(
                    name=name,
                    available=True,
                    status="available",
                    executable=launcher,
                    description="DeepSeek Harness (auto-discovered source checkout)",
                    details={"auto_discovered": True, "repo": str(found_repo)},
                )

        if found_binary is not None:
            launcher = _install_binary_launcher(name, found_binary)
            if launcher:
                return HarnessInfo(
                    name=name,
                    available=True,
                    status="available",
                    executable=launcher,
                    description=f"{name} (auto-discovered)",
                    details={"auto_discovered": True, "source": str(found_binary)},
                )

        if name == "agy" and found_binary is not None:
            # A discovered AGY binary is a CLI worker.  The local
            # wrapper-antigravity proxy is intentionally a separate product
            # and must not be synthesized into an AGY CLI launcher.
            launcher = _install_binary_launcher(name, found_binary)
            if launcher:
                return HarnessInfo(
                    name=name,
                    available=True,
                    status="available",
                    executable=launcher,
                    description="AGY / Antigravity CLI (auto-discovered)",
                    details={"auto_discovered": True, "source": str(found_binary)},
                )

        # Nothing usable: return a diagnostic carrying the fallback guidance.
        guidance = (
            f"Executable '{name}' was not found on PATH, env, or local filesystem. "
            f"HAOS will continue with the native local worker. "
            f"To enable it, install the tool or export one of: "
            f"{_env_hint(name)}"
        )
        logger.warning("Harness '%s' auto-discovery failed: %s", name, guidance)
        return HarnessInfo(
            name=name,
            available=False,
            status="missing",
            description=f"{name} (not found)",
            error=guidance,
            details={"fallback": "native", "searched": True},
        )


def _env_hint(name: str) -> str:
    hints = {
        "dsh": "DSH_PATH",
        "opencode": "OPENCODE_PATH",
        "agy": "AGY_PATH (the CLI; wrapper-antigravity proxy is separate)",
        "codex": "CODEX_PATH",
        "claude-code": "CLAUDE_CODE_PATH",
        "acp": "PATH (hermes-acp)",
    }
    return hints.get(name, f"{name.upper()}_PATH")


_singleton: Optional[HarnessAutoDiscovery] = None


def get_discovery() -> HarnessAutoDiscovery:
    global _singleton
    if _singleton is None:
        _singleton = HarnessAutoDiscovery()
    return _singleton
