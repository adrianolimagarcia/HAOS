"""Appliance assets that live outside the Python tree the updater pulls.

``haos update`` swaps the checkout and reinstalls dependencies, but two asset classes ship
*beyond* that tree and go stale silently — the Python side is current, so nothing looks wrong
while the binaries lag:

* the vendored ``haos-edge`` binary, which ``haos status`` and ``haos team`` execute, and
* the installer-managed scripts under ``HAOS_HOME/scripts/``.

Observed 19/09/2026 on this appliance: the installed ``haos-edge`` was six commits behind the
source (including a WebUI auth fix and a RAG search fix) and ``haos_memory_populate.py`` was
behind the repo's copy, both because only ``scripts/install_haos.sh`` ever wrote them.

Every install is skipped when the destination already matches, so a routine update copies
nothing; the whole step is a no-op on an install that has neither (upstream, containers).
"""

from __future__ import annotations

import filecmp
import shutil
from pathlib import Path
from typing import Optional

# The installer's constant, not a guess: the appliance is a system-wide install and the `haos`
# entry point lives here. A destination that is absent or unwritable is reported, never forced.
APPLIANCE_BIN_DIR = Path("/usr/local/bin")

# Vendored in-tree so the ISO and the running appliance carry the same artifact. Rebuilt by
# `packages/haos-edge/build.sh`, which remaps build-machine paths the release binary must not leak.
HAOS_EDGE_VENDOR_REL = Path("distro/haos-linux/config/includes.chroot/usr/local/bin/haos-edge")

# Copied into HAOS_HOME/scripts/ by scripts/install_haos.sh. The nightly timer and the memory
# routine resolve them from there, so a stale copy is stale automation.
APPLIANCE_SCRIPTS = ("haos_nightly_maintenance.sh", "haos_memory_populate.py")


def _replace_if_changed(src: Path, dst: Path) -> Optional[str]:
    """Copy *src* over *dst* when the content differs. Returns a report line, else None."""
    if not src.is_file():
        return None
    try:
        if dst.is_file() and filecmp.cmp(src, dst, shallow=False):
            return None
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError as exc:
        return f"  ⚠ {dst.name}: not updated ({exc.strerror or exc})"
    return f"  ✓ {dst.name} ← {src.parent}"


def sync_appliance_assets(
    *,
    project_root: Optional[Path] = None,
    bin_dir: Optional[Path] = None,
    scripts_home: Optional[Path] = None,
) -> list[str]:
    """Bring the appliance's non-Python assets in line with the checkout just pulled.

    Returns one line per asset touched; empty when everything already matched (the common case,
    and the entire result on an upstream or container install). Never raises: a caller runs this
    after the code swap, where an exception would abort the fleet verification behind it.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent
    if bin_dir is None:
        bin_dir = APPLIANCE_BIN_DIR
    if scripts_home is None:
        from hermes_constants import get_hermes_home

        scripts_home = get_hermes_home() / "scripts"

    changed: list[str] = []
    edge = _replace_if_changed(project_root / HAOS_EDGE_VENDOR_REL, bin_dir / "haos-edge")
    if edge:
        changed.append(edge)
    # Only a directory the installer already made belongs to the appliance: creating it here would
    # grow an empty scripts/ dir on every upstream install that has no use for one.
    if scripts_home.is_dir():
        for name in APPLIANCE_SCRIPTS:
            line = _replace_if_changed(project_root / "scripts" / name, scripts_home / name)
            if line:
                changed.append(line)
    return changed
