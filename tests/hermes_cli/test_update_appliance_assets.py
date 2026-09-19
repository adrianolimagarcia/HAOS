"""Appliance assets must follow the checkout the updater pulls.

Regression for the 19/09/2026 finding: `haos update` refreshed the Python tree while the
installed `haos-edge` stayed six commits behind — `haos status` and `haos team` ran old code,
including a WebUI auth fix — because only `scripts/install_haos.sh` ever wrote that binary.
"""

from __future__ import annotations

from pathlib import Path

from hermes_cli.update_cmd_assets import HAOS_EDGE_VENDOR_REL, sync_appliance_assets


def _fake_install(tmp_path: Path, *, edge: bytes | None, scripts: dict[str, str] | None = None):
    """A checkout plus the two destinations, mirroring what install_haos.sh lays down."""
    root = tmp_path / "checkout"
    (root / HAOS_EDGE_VENDOR_REL).parent.mkdir(parents=True)
    if edge is not None:
        (root / HAOS_EDGE_VENDOR_REL).write_bytes(edge)
    (root / "scripts").mkdir()
    for name, body in (scripts or {}).items():
        (root / "scripts" / name).write_text(body)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return root, bin_dir, tmp_path / "home" / "scripts"


def test_a_stale_edge_binary_is_replaced_and_the_step_is_idempotent(tmp_path):
    root, bin_dir, scripts_home = _fake_install(tmp_path, edge=b"new edge")
    installed = bin_dir / "haos-edge"
    installed.write_bytes(b"old edge")

    changed = sync_appliance_assets(project_root=root, bin_dir=bin_dir, scripts_home=scripts_home)

    assert installed.read_bytes() == b"new edge"
    assert len(changed) == 1 and "haos-edge" in changed[0]
    # Second run: the appliance now matches the checkout, so a routine update copies nothing.
    assert sync_appliance_assets(project_root=root, bin_dir=bin_dir, scripts_home=scripts_home) == []


def test_an_install_without_the_appliance_assets_is_left_alone(tmp_path):
    root, bin_dir, scripts_home = _fake_install(tmp_path, edge=None)

    assert sync_appliance_assets(project_root=root, bin_dir=bin_dir, scripts_home=scripts_home) == []
    # install_haos.sh owns this directory; an upstream install must not grow one.
    assert not scripts_home.exists()
    assert list(bin_dir.iterdir()) == []


def test_scripts_follow_the_checkout_only_where_the_installer_made_the_directory(tmp_path):
    root, bin_dir, scripts_home = _fake_install(
        tmp_path, edge=b"edge", scripts={"haos_memory_populate.py": "new body"}
    )
    scripts_home.mkdir(parents=True)
    (scripts_home / "haos_memory_populate.py").write_text("old body")

    changed = sync_appliance_assets(project_root=root, bin_dir=bin_dir, scripts_home=scripts_home)

    assert (scripts_home / "haos_memory_populate.py").read_text() == "new body"
    assert any("haos_memory_populate.py" in line for line in changed)
    # No source in the checkout means nothing is invented at the destination.
    assert not (scripts_home / "haos_nightly_maintenance.sh").exists()


def test_a_failing_destination_is_reported_not_raised(tmp_path):
    """The step runs after the code swap; an exception would abort the fleet verification behind it."""
    root, bin_dir, scripts_home = _fake_install(tmp_path, edge=b"edge")
    bin_dir.rmdir()
    bin_dir.write_text("a file where the bin directory belongs")  # mkdir(parents=True) raises

    changed = sync_appliance_assets(project_root=root, bin_dir=bin_dir, scripts_home=scripts_home)

    assert changed and "not updated" in changed[0]
