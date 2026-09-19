"""scripts/check_dox_coverage.py enforces the per-file contract document as a ratchet.

The rule this guards: a module in a governed scope must ship a ``<name>.py.dox.md`` sibling carrying
Purpose / Contract / Side effects / Verification, each with body text. Retrofitting that onto the
modules that predate the rule would be a wall of stubs, so ``scripts/ci/dox_coverage_baseline.txt``
grandfathers them and only un-grandfathered files are required to ship docs. The baseline may only
tighten: a grandfathered module that gains its doc must be dropped from the baseline, so a stale
baseline is itself a failure rather than silent drift.
"""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_dox_coverage.py"

COMPLETE = """\
# tools/thing.py — contract

## Purpose
Owns the thing and nothing else.

## Contract
`thing()` returns a thing; callers may rely on it not raising.

## Side effects
None.

## Verification
scripts/run_tests.sh tests/tools/test_thing.py
"""


def _load():
    spec = importlib.util.spec_from_file_location("check_dox_coverage", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses/annotations resolve through sys.modules[cls.__module__] (3.11).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _module(tmp_path, name="tools/thing.py", doc=None):
    py = tmp_path / name
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("def thing():\n    return 1\n", encoding="utf-8")
    if doc is not None:
        py.with_name(py.name + ".dox.md").write_text(doc, encoding="utf-8")
    return py


def _main_in(tmp_path, mod, monkeypatch, argv):
    """Run main() against a throwaway tree, so the real baseline is never touched."""
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "BASELINE", tmp_path / "baseline.txt")
    return mod.main(argv)


def test_complete_doc_satisfies_the_contract(tmp_path):
    mod = _load()
    py = _module(tmp_path, doc=COMPLETE)
    result = mod.evaluate([py], baseline=set(), root=tmp_path)
    assert result["documented"] == ["tools/thing.py"]
    assert not result["malformed"] and not result["gap"]


def test_undocumented_module_is_a_gap(tmp_path):
    mod = _load()
    py = _module(tmp_path)
    result = mod.evaluate([py], baseline=set(), root=tmp_path)
    assert result["gap"] == ["tools/thing.py"]
    assert not result["documented"]


def test_grandfathered_module_is_not_a_gap(tmp_path):
    mod = _load()
    py = _module(tmp_path)
    result = mod.evaluate([py], baseline={"tools/thing.py"}, root=tmp_path)
    assert result["grandfathered"] == ["tools/thing.py"]
    assert not result["gap"]


def test_missing_section_is_malformed(tmp_path):
    mod = _load()
    doc = COMPLETE.replace("## Verification\nscripts/run_tests.sh tests/tools/test_thing.py\n", "")
    py = _module(tmp_path, doc=doc)
    result = mod.evaluate([py], baseline=set(), root=tmp_path)
    assert not result["documented"]
    assert result["malformed"] == ["tools/thing.py: missing verification"]


def test_heading_without_body_is_malformed(tmp_path):
    """A heading-only section satisfies a grep while telling the reader nothing — the anti-stub rule."""
    mod = _load()
    doc = COMPLETE.replace("## Purpose\nOwns the thing and nothing else.\n", "## Purpose\n")
    py = _module(tmp_path, doc=doc)
    assert mod.missing_sections(doc) == ["purpose"]
    result = mod.evaluate([py], baseline=set(), root=tmp_path)
    assert result["malformed"] == ["tools/thing.py: missing purpose"]


def test_documented_module_left_in_the_baseline_is_stale(tmp_path):
    mod = _load()
    py = _module(tmp_path, doc=COMPLETE)
    result = mod.evaluate([py], baseline={"tools/thing.py"}, root=tmp_path)
    assert result["stale_baseline"] == ["tools/thing.py"]


def test_scope_walk_skips_package_markers_and_generated_dirs(tmp_path):
    mod = _load()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "real.py").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "tools" / "__pycache__").mkdir()
    (tmp_path / "tools" / "__pycache__" / "cached.py").write_text("", encoding="utf-8")
    found = {p.name for p in mod.iter_scope_files(["tools"], root=tmp_path)}
    assert found == {"real.py"}


def test_baseline_round_trip_is_sorted_and_ignores_comments(tmp_path):
    mod = _load()
    path = tmp_path / "baseline.txt"
    mod.write_baseline(["tools/b.py", "tools/a.py", "tools/a.py"], path)
    assert mod.load_baseline(path) == {"tools/a.py", "tools/b.py"}


def test_missing_baseline_means_nothing_is_grandfathered(tmp_path):
    mod = _load()
    assert mod.load_baseline(tmp_path / "absent.txt") == set()


def test_new_undocumented_module_fails_then_passes_once_grandfathered(tmp_path, monkeypatch):
    mod = _load()
    _module(tmp_path)
    assert _main_in(tmp_path, mod, monkeypatch, []) == 1
    assert _main_in(tmp_path, mod, monkeypatch, ["--update-baseline"]) == 0
    assert _main_in(tmp_path, mod, monkeypatch, []) == 0


def test_documenting_a_grandfathered_module_requires_tightening_the_baseline(tmp_path, monkeypatch):
    mod = _load()
    py = _module(tmp_path)
    assert _main_in(tmp_path, mod, monkeypatch, ["--update-baseline"]) == 0

    py.with_name(py.name + ".dox.md").write_text(COMPLETE, encoding="utf-8")
    assert _main_in(tmp_path, mod, monkeypatch, []) == 1  # stale baseline is a failure

    assert _main_in(tmp_path, mod, monkeypatch, ["--update-baseline"]) == 0
    assert _main_in(tmp_path, mod, monkeypatch, []) == 0
    assert mod.load_baseline(tmp_path / "baseline.txt") == set()


def test_report_never_fails_so_the_backlog_is_visible(tmp_path, monkeypatch):
    mod = _load()
    _module(tmp_path)
    assert _main_in(tmp_path, mod, monkeypatch, ["--report"]) == 0


def test_unknown_path_in_files_mode_is_a_usage_error(tmp_path, monkeypatch):
    mod = _load()
    assert _main_in(tmp_path, mod, monkeypatch, ["--files", "tools/absent.py"]) == 2


def test_committed_baseline_matches_the_tree():
    """The shipped ratchet must be honest: no entry for a deleted module, none for a documented one."""
    mod = _load()
    baseline = mod.load_baseline()
    assert baseline, "the committed baseline must grandfather the modules that predate the rule"

    present = {mod.rel(p) for p in mod.iter_scope_files(mod.DEFAULT_SCOPE)}
    absent = sorted(baseline - present)
    assert not absent, f"baseline lists modules that no longer exist: {absent[:10]}"

    documented = sorted(n for n in baseline if mod.dox_path(mod.ROOT / n).exists())
    assert not documented, (
        f"baseline lists modules that are now documented — run "
        f"`python scripts/check_dox_coverage.py --update-baseline`: {documented[:10]}"
    )
