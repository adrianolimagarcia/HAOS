"""scripts/check_timing_bounds.py flags tight wall-clock bounds, not counts.

The rule lives in ``AGENTS.md``: a wall-clock upper bound under 2s cannot survive
``scripts/run_tests.sh``'s 16-way parallel runner. The lint is a ratchet, so the two
things that must hold are (a) the detector separates elapsed time from the
count/byte assertions that merely look temporal, and (b) a bound that is not in the
baseline fails while a baseline entry whose bound disappeared also fails.
"""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_timing_bounds.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_timing_bounds", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolve string annotations through sys.modules[cls.__module__] (3.11).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _write(tmp_path: Path, body: str) -> Path:
    probe = tmp_path / "tests" / "test_probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(body, encoding="utf-8")
    return probe


def test_tight_bound_is_flagged_but_counts_and_wide_bounds_are_not(tmp_path: Path) -> None:
    mod = _load()
    probe = _write(
        tmp_path,
        "import time\n"
        "\n"
        "\n"
        "def test_tight_elapsed():\n"
        "    t0 = time.monotonic()\n"
        "    time.sleep(0)\n"
        "    assert time.monotonic() - t0 < 0.5\n"
        "\n"
        "\n"
        "def test_wide_elapsed():\n"
        "    t0 = time.monotonic()\n"
        "    time.sleep(0)\n"
        "    assert time.monotonic() - t0 < 5.0\n"
        "\n"
        "\n"
        "def test_count_is_not_a_clock():\n"
        "    result = {'total_count': 3}\n"
        "    assert result['total_count'] < 1\n",
    )

    findings = mod.scan_file(probe, tmp_path)
    keys = {f.key for f in findings}
    assert keys == {"tests/test_probe.py::test_tight_elapsed"}, keys

    # The 2s floor is inclusive: 2.0 is acceptable, 1.999 is not.
    assert mod.MIN_BOUND_SECONDS == 2.0
    assert findings[0].bound == 0.5


def test_ratchet_fails_on_a_new_bound_and_on_a_stale_entry(tmp_path: Path) -> None:
    mod = _load()
    probe = _write(
        tmp_path,
        "def test_something():\n"
        "    elapsed = 0.1\n"
        "    assert elapsed < 1.0\n",
    )
    key = "tests/test_probe.py::test_something"

    # Not grandfathered -> new -> the ratchet fails.
    fresh = mod.evaluate([probe], baseline=set(), root=tmp_path)
    assert [f.key for f in fresh["new"]] == [key]
    assert fresh["stale_baseline"] == []

    # Grandfathered -> silent.
    frozen = mod.evaluate([probe], baseline={key}, root=tmp_path)
    assert frozen["new"] == []
    assert [f.key for f in frozen["grandfathered"]] == [key]

    # Baseline entry whose bound is gone -> stale -> the ratchet must tighten.
    stale = mod.evaluate([probe], baseline={key, "tests/gone.py::test_gone"}, root=tmp_path)
    assert stale["stale_baseline"] == ["tests/gone.py::test_gone"]


def test_baseline_round_trips_through_the_ratchet_file(tmp_path: Path) -> None:
    mod = _load()
    path = tmp_path / "baseline.txt"
    mod.write_baseline({"tests/b.py::test_two", "tests/a.py::test_one"}, path)

    assert mod.load_baseline(path) == {"tests/a.py::test_one", "tests/b.py::test_two"}
    # Comments are not entries.
    assert all(not line.startswith("#") for line in mod.load_baseline(path))
