"""release.py must stamp bootstrap-installer versions with the release semver.

Tauri CFBundleShortVersionString is read from
apps/bootstrap-installer/src-tauri/tauri.conf.json (and the sibling
package.json). Those files were hardcoded 0.0.1 and omitted from
update_version_files / the --publish --bump git add list, so Hermes-Setup.dmg
always shipped 0.0.1. Same class as the desktop stamp (#68783 / PR #68796).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _load():
    spec = importlib.util.spec_from_file_location(
        "release_bootstrap_installer_version", SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


release = _load()


def _json_version(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["version"]


def _patch_repo(tmp_path, monkeypatch, *, with_installer: bool = True):
    repo = tmp_path
    init_py = repo / "hermes_cli" / "__init__.py"
    init_py.parent.mkdir(parents=True)
    init_py.write_text(
        '__version__ = "0.0.1"\n__release_date__ = "2026.1.1"\n',
        encoding="utf-8",
    )
    pyproject = repo / "pyproject.toml"
    pyproject.write_text('version = "0.0.1"\n', encoding="utf-8")

    desktop_pkg = repo / "apps" / "desktop" / "package.json"
    desktop_pkg.parent.mkdir(parents=True)
    desktop_pkg.write_text('{"version":"0.0.1"}\n', encoding="utf-8")

    installer_pkg = repo / "apps" / "bootstrap-installer" / "package.json"
    tauri_conf = (
        repo / "apps" / "bootstrap-installer" / "src-tauri" / "tauri.conf.json"
    )
    cargo_toml = repo / "apps" / "bootstrap-installer" / "src-tauri" / "Cargo.toml"
    if with_installer:
        tauri_conf.parent.mkdir(parents=True)
        installer_pkg.write_text(
            '{"name":"x","version":"0.0.1"}\n', encoding="utf-8"
        )
        tauri_conf.write_text(
            '{"productName":"Hermes","version":"0.0.1"}\n', encoding="utf-8"
        )
        cargo_toml.write_text('[package]\nversion = "0.0.1"\n', encoding="utf-8")

    monkeypatch.setattr(release, "REPO_ROOT", repo)
    monkeypatch.setattr(release, "VERSION_FILE", init_py)
    monkeypatch.setattr(release, "PYPROJECT_FILE", pyproject)
    return {
        "repo": repo,
        "init_py": init_py,
        "pyproject": pyproject,
        "desktop_pkg": desktop_pkg,
        "installer_pkg": installer_pkg,
        "tauri_conf": tauri_conf,
        "cargo_toml": cargo_toml,
    }


def test_update_version_files_stamps_bootstrap_installer(tmp_path, monkeypatch):
    paths = _patch_repo(tmp_path, monkeypatch)

    release.update_version_files("0.21.1", "2026.9.10")

    assert _json_version(paths["installer_pkg"]) == "0.21.1"
    assert _json_version(paths["tauri_conf"]) == "0.21.1"
    assert 'version = "0.21.1"' in paths["cargo_toml"].read_text(encoding="utf-8")

    # CONTROL: existing desktop / Python stamps still happen.
    assert _json_version(paths["desktop_pkg"]) == "0.21.1"
    assert 'version = "0.21.1"' in paths["pyproject"].read_text(encoding="utf-8")
    init_text = paths["init_py"].read_text(encoding="utf-8")
    assert '__version__ = "0.21.1"' in init_text
    assert '__release_date__ = "2026.9.10"' in init_text


def test_update_version_files_skips_missing_installer_dir(tmp_path, monkeypatch):
    paths = _patch_repo(tmp_path, monkeypatch, with_installer=False)

    release.update_version_files("0.21.1", "2026.9.10")

    assert not paths["installer_pkg"].exists()
    assert not paths["tauri_conf"].exists()
    assert _json_version(paths["desktop_pkg"]) == "0.21.1"
    assert 'version = "0.21.1"' in paths["pyproject"].read_text(encoding="utf-8")
    assert '__version__ = "0.21.1"' in paths["init_py"].read_text(encoding="utf-8")


def test_update_version_files_does_not_invent_version_keys(tmp_path, monkeypatch):
    paths = _patch_repo(tmp_path, monkeypatch)
    original = '{"name":"x","productName":"Hermes"}\n'
    paths["installer_pkg"].write_text(original, encoding="utf-8")
    paths["tauri_conf"].write_text(original, encoding="utf-8")

    release.update_version_files("0.21.1", "2026.9.10")

    assert paths["installer_pkg"].read_text(encoding="utf-8") == original
    assert paths["tauri_conf"].read_text(encoding="utf-8") == original
    assert _json_version(paths["desktop_pkg"]) == "0.21.1"


def test_version_files_to_stage_includes_installer_when_present(tmp_path, monkeypatch):
    paths = _patch_repo(tmp_path, monkeypatch)

    staged = release.version_files_to_stage()

    assert str(paths["init_py"]) in staged
    assert str(paths["pyproject"]) in staged
    assert str(paths["desktop_pkg"]) in staged
    assert str(paths["installer_pkg"]) in staged
    assert str(paths["tauri_conf"]) in staged
    assert str(paths["cargo_toml"]) in staged


def test_version_files_to_stage_omits_missing_installer(tmp_path, monkeypatch):
    paths = _patch_repo(tmp_path, monkeypatch, with_installer=False)

    staged = release.version_files_to_stage()

    assert str(paths["installer_pkg"]) not in staged
    assert str(paths["tauri_conf"]) not in staged
    assert str(paths["cargo_toml"]) not in staged
    assert str(paths["desktop_pkg"]) in staged
    assert str(paths["init_py"]) in staged
    assert str(paths["pyproject"]) in staged


def test_bump_only_writes_the_bump_and_never_reaches_the_release_machinery(
    tmp_path, monkeypatch, capsys
):
    """`--bump-only` advances the version without committing, tagging or publishing.

    Before this mode existed the ONLY path that wrote a bump was `--publish`, which
    also commits, creates an annotated tag and publishes a release — far more than
    "increment by one on every commit" wants. The three release helpers are booby
    trapped rather than merely asserted-absent, so the test fails if the early
    return is ever moved back below them.
    """
    paths = _patch_repo(tmp_path, monkeypatch)

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("--bump-only must not reach the release/tag machinery")

    monkeypatch.setattr(release, "next_available_tag", _must_not_run)
    monkeypatch.setattr(release, "get_last_tag", _must_not_run)
    monkeypatch.setattr(release, "get_commits", _must_not_run)
    monkeypatch.setattr(
        sys, "argv", ["release.py", "--bump", "patch", "--bump-only", "--date", "2026.9.19"]
    )

    release.main()

    init_text = paths["init_py"].read_text(encoding="utf-8")
    assert '__version__ = "0.0.2"' in init_text
    assert '__release_date__ = "2026.9.19"' in init_text
    assert 'version = "0.0.2"' in paths["pyproject"].read_text(encoding="utf-8")
    assert _json_version(paths["desktop_pkg"]) == "0.0.2"
    assert "Bumped 0.0.1 -> 0.0.2" in capsys.readouterr().out


def test_bump_only_without_a_bump_part_is_an_error(tmp_path, monkeypatch):
    """Writing "the next version" needs to know which component moves."""
    _patch_repo(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["release.py", "--bump-only"])

    with pytest.raises(SystemExit):
        release.main()
