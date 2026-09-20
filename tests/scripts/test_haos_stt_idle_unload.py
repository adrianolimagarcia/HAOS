"""Tests for the HAOS STT idle-unload behaviour.

Regression for the "the model never leaves VRAM" bug: `_maybe_unload()` used to run in the
handler's `finally`, i.e. immediately after `_get_model()` had refreshed `last_used`. The
idle delta was therefore ~0s and the condition never held, so on the 4 GB card shared with
docling-serve the model stayed resident forever. The check now runs on a background task
where `last_used` is genuinely in the past.
"""

import time

import pytest

from scripts import haos_stt_server as stt


@pytest.fixture
def idle_window(monkeypatch):
    """Fix the idle window so the tests do not depend on config.yaml."""
    monkeypatch.setattr(stt, "_unload_after_idle_seconds", lambda: 300)
    return 300


@pytest.fixture(autouse=True)
def clean_state():
    stt._state.update({"model": None, "key": None, "last_used": 0.0, "effective": None, "inflight": 0})
    yield
    stt._state.update({"model": None, "key": None, "last_used": 0.0, "effective": None, "inflight": 0})


def _load(seconds_ago: float, inflight: int = 0):
    stt._state.update({
        "model": object(),
        "key": ("large-v3", "cuda", "int8"),
        "effective": {"model": "large-v3"},
        "last_used": time.time() - seconds_ago,
        "inflight": inflight,
    })


class TestIdleUnload:
    def test_releases_after_the_idle_window(self, idle_window):
        _load(seconds_ago=10_000)
        stt._maybe_unload()
        assert stt._state["model"] is None

    def test_keeps_model_within_the_window(self, idle_window):
        _load(seconds_ago=0)
        stt._maybe_unload()
        assert stt._state["model"] is not None

    def test_never_releases_while_a_request_is_inflight(self, idle_window):
        """A request in progress must not lose the model under it."""
        _load(seconds_ago=10_000, inflight=1)
        stt._maybe_unload()
        assert stt._state["model"] is not None

    def test_disabled_window_never_releases(self, monkeypatch):
        monkeypatch.setattr(stt, "_unload_after_idle_seconds", lambda: 0)
        _load(seconds_ago=10_000)
        stt._maybe_unload()
        assert stt._state["model"] is not None

    def test_release_clears_effective_so_health_stops_lying(self, idle_window):
        _load(seconds_ago=10_000)
        stt._maybe_unload()
        assert stt._state["effective"] is None
        assert stt._state["key"] is None

    def test_release_is_idempotent(self, idle_window):
        _load(seconds_ago=10_000)
        stt._maybe_unload()
        stt._maybe_unload()  # second pass with no model must be a no-op
        assert stt._state["model"] is None


class TestIdleUnloadIsIndependentOfRequestTiming:
    """The core of the regression: freshness at call time must not mask idleness."""

    def test_check_right_after_use_does_not_release(self, idle_window):
        # `_get_model()` stamps last_used on every call — the old call site.
        _load(seconds_ago=0)
        stt._maybe_unload()
        assert stt._state["model"] is not None

    def test_same_state_releases_once_the_window_elapses(self, idle_window):
        _load(seconds_ago=0)
        stt._maybe_unload()
        assert stt._state["model"] is not None

        # Same process, later: the background loop is what makes the release possible.
        stt._state["last_used"] = time.time() - (idle_window + 1)
        stt._maybe_unload()
        assert stt._state["model"] is None


def test_lifespan_starts_and_stops_the_idle_task(idle_window):
    """The loop is wired into the app lifespan and shuts down without leaking a task."""
    import asyncio

    async def run():
        async with stt._lifespan(stt.app):
            await asyncio.sleep(0.05)
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            assert pending, "expected the idle-unload task to be running"
        await asyncio.sleep(0.05)
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert not leftover, f"idle task not cancelled on shutdown: {leftover}"

    asyncio.run(run())


def test_lifespan_skips_the_task_when_unload_is_disabled(monkeypatch):
    import asyncio

    monkeypatch.setattr(stt, "_unload_after_idle_seconds", lambda: 0)

    async def run():
        async with stt._lifespan(stt.app):
            await asyncio.sleep(0.05)
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            assert not pending, "no idle task should start when the window is 0"

    asyncio.run(run())


def test_should_exit_on_idle_respects_systemd_and_env(monkeypatch):
    monkeypatch.delenv("LISTEN_FDS", raising=False)
    monkeypatch.delenv("HAOS_STT_EXIT_ON_IDLE", raising=False)
    assert not stt._should_exit_on_idle()

    monkeypatch.setenv("LISTEN_FDS", "1")
    assert stt._should_exit_on_idle()

    monkeypatch.delenv("LISTEN_FDS", raising=False)
    monkeypatch.setenv("HAOS_STT_EXIT_ON_IDLE", "1")
    assert stt._should_exit_on_idle()


def test_maybe_unload_exits_when_socket_activation_enabled(monkeypatch, idle_window):
    """When running under socket activation, idleness triggers clean process exit."""
    monkeypatch.setenv("HAOS_STT_EXIT_ON_IDLE", "1")
    _load(seconds_ago=10_000)

    exited = []
    monkeypatch.setattr(stt.os, "_exit", lambda code: exited.append(code))

    stt._maybe_unload()
    assert exited == [0]
    assert stt._state["model"] is None

