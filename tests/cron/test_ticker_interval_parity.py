"""Parity guard: TICKER_INTERVAL_SECONDS (cron/jobs.py) is the ACTUAL ticker interval.

Regression guard for M3 (review 2026-10-18): the 2026-10-13 CPU package changed the
constant 60s -> 120s, but the scheduler default stayed 60s and the gateway caller never
passed it — so production ticked every 60s while `haos cron status` computed staleness
against 120s. If any of these drift again, this suite fails.
"""
import ast
import inspect
import threading
from pathlib import Path

from cron.jobs import TICKER_INTERVAL_SECONDS
from cron.scheduler_provider import InProcessCronScheduler

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_constant_is_120():
    assert TICKER_INTERVAL_SECONDS == 120


def test_start_default_matches_constant():
    default = inspect.signature(InProcessCronScheduler.start).parameters["interval"].default
    assert default == TICKER_INTERVAL_SECONDS, (
        f"InProcessCronScheduler.start default interval={default!r} != "
        f"TICKER_INTERVAL_SECONDS={TICKER_INTERVAL_SECONDS}"
    )


def test_start_multiplex_default_matches_constant():
    default = inspect.signature(
        InProcessCronScheduler._start_multiplex).parameters["interval"].default
    assert default == TICKER_INTERVAL_SECONDS, (
        f"_start_multiplex default interval={default!r} != "
        f"TICKER_INTERVAL_SECONDS={TICKER_INTERVAL_SECONDS}"
    )


def test_effective_tick_wait_matches_constant(monkeypatch):
    """Behavior, not just the signature: the running loop waits TICKER_INTERVAL_SECONDS.

    Ticks and store writes are stubbed (same pattern as the existing ticker tests);
    a recording stop_event captures the first sleep and exits the loop.
    """
    monkeypatch.setattr("cron.scheduler.tick", lambda *a, **k: 0)
    monkeypatch.setattr("cron.jobs.record_ticker_heartbeat", lambda *a, **k: None)
    monkeypatch.setattr("cron.jobs.record_ticker_error", lambda *a, **k: None)
    monkeypatch.setattr("cron.jobs.clear_ticker_error", lambda *a, **k: None)

    waits = []

    class _RecEvent(threading.Event):
        def wait(self, timeout=None):
            waits.append(timeout)
            self.set()
            return True

    stop = _RecEvent()
    prov = InProcessCronScheduler()
    monkeypatch.setattr(prov, "recover_interrupted", lambda: 0)
    prov.start(stop, adapters=None, loop=None)  # no explicit interval: must use the constant
    assert waits, "ticker loop never reached its sleep"
    assert waits[0] == TICKER_INTERVAL_SECONDS, f"first tick wait={waits[0]!r}"


def test_gateway_caller_wires_constant():
    """gateway/run.py must pass interval=TICKER_INTERVAL_SECONDS to the scheduler.

    AST-based so formatting changes don't break it; a structural change to the call
    site (e.g. building kwargs differently) fails honestly and needs a test update.
    """
    tree = ast.parse((REPO_ROOT / "gateway" / "run.py").read_text(encoding="utf-8"))
    func = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_start_gateway_start_cron_and_housekeeping"
    )
    dicts = [n for n in ast.walk(func)
             if isinstance(n, ast.Dict)
             and any(isinstance(k, ast.Constant) and k.value == "adapters" for k in n.keys)]
    assert dicts, "cron_start_kwargs dict literal not found in _start_gateway_start_cron_and_housekeeping"
    keys = {k.value for d in dicts for k in d.keys if isinstance(k, ast.Constant)}
    assert "interval" in keys, (
        f"cron_start_kwargs keys={sorted(keys)}: 'interval' not wired at the gateway call site"
    )


def test_status_staleness_derives_from_constant():
    """haos cron status staleness math must derive from the same constant."""
    import hermes_cli.cron as cron_cli
    src = inspect.getsource(cron_cli._print_ticker_health)
    assert "TICKER_INTERVAL_SECONDS" in src
    # and the human-facing comment must not still describe the old 60s cadence
    assert "200s @ 60s" not in src
