"""Regression coverage for logical cwd propagation in deferred TUI/Desktop builds."""

import threading
import time
import uuid
from types import SimpleNamespace

from tui_gateway import server


def _run_deferred_build(monkeypatch, tmp_path, *, slow_build_tail_seconds=0.0):
    """Drive one deferred build to completion; return ``(captured, workspace)``.

    ``slow_build_tail_seconds`` stalls the post-construction tail
    (``_announce_built_agent`` -> ``server._session_info``), standing in for the tail's
    load-dependent cold cost (see ``test_agent_ready_is_not_bounded_by_a_fixed_budget``).
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured = {}
    built = threading.Event()
    ready = threading.Event()
    sid = f"cwd-build-{uuid.uuid4().hex[:8]}"
    session = {
        "agent_ready": ready,
        "session_key": f"cwd-key-{uuid.uuid4().hex[:8]}",
        "cwd": str(workspace),
    }

    def fake_set_session_context(key, cwd=None):
        captured["context_key"] = key
        captured["context_cwd"] = cwd
        return []

    def fake_make_agent(*args, **kwargs):
        captured["agent_cwd"] = kwargs.get("cwd_override")
        built.set()
        return SimpleNamespace(model="test", session_id=session["session_key"])

    monkeypatch.setattr(server, "_set_session_context", fake_set_session_context)
    monkeypatch.setattr(server, "_clear_session_context", lambda _tokens: None)
    monkeypatch.setattr(server, "_make_agent", fake_make_agent)
    monkeypatch.setattr(
        "tui_gateway.entry.ensure_mcp_discovery_started", lambda: None
    )
    monkeypatch.setattr(server, "_wire_callbacks", lambda _sid: None)
    monkeypatch.setattr(server, "_config_model_target", lambda: ("", ""))
    monkeypatch.setattr(server, "_start_notification_poller", lambda *a, **k: None)
    monkeypatch.setattr(server, "_schedule_mcp_late_refresh", lambda *a, **k: None)
    monkeypatch.setattr(server, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(server, "_notify_session_boundary", lambda *a, **k: None)

    if slow_build_tail_seconds:
        announce = server._announce_built_agent

        def slow_announce(*args, **kwargs):
            time.sleep(slow_build_tail_seconds)
            return announce(*args, **kwargs)

        monkeypatch.setattr(server, "_announce_built_agent", slow_announce)

    server._sessions[sid] = session
    try:
        server._start_agent_build(sid, session)
        assert built.wait(timeout=15), "agent build thread never called _make_agent"
        # No wall-clock deadline: the tail's cost is one-time lazy imports
        # (``tools.approval`` / ``model_tools`` inside ``_session_info``), measured at
        # 1.5-3.9s idle and >5s reachable on a loaded runner. ``_build`` sets
        # ``agent_ready`` from its ``finally``, so the wait only returns once the build
        # is really finished; a hang is caught by the runner's per-file timeout.
        assert ready.wait(), "agent_ready never set after build"
    finally:
        server._sessions.pop(sid, None)
        from tools.approval import unregister_gateway_notify

        unregister_gateway_notify(session["session_key"])

    return captured, workspace


def test_deferred_agent_build_threads_session_cwd(monkeypatch, tmp_path):
    """A cold Desktop build must not fall back to the serve process cwd."""
    captured, workspace = _run_deferred_build(monkeypatch, tmp_path)

    assert captured["context_cwd"] == str(workspace)
    assert captured["agent_cwd"] == str(workspace)


# Longer than the fixed 5s budget this file used to impose on ``agent_ready``.
_SLOW_BUILD_TAIL_SECONDS = 5.5


def test_agent_ready_is_not_bounded_by_a_fixed_budget(monkeypatch, tmp_path):
    """A build with a slow tail must still reach ``agent_ready``.

    Regression for the full-suite flake: waiting on ``agent_ready`` with
    ``timeout=5`` raced the build tail, which pays one-time lazy imports
    (``tools.approval`` and ``model_tools``, the latter inside ``server._session_info``)
    before the event is set. Pre-importing those modules collapsed the measured
    built->ready gap from 1.5-3.9s to 0.5-0.7s, and the pre-import alone cost 1.4-5.3s
    across runs — so the 5s budget is reachable by ordinary warm-up on a loaded runner.
    The contract is that ``agent_ready`` is eventually set, not that it is set within a
    fixed budget.
    """
    captured, workspace = _run_deferred_build(
        monkeypatch, tmp_path, slow_build_tail_seconds=_SLOW_BUILD_TAIL_SECONDS
    )

    assert captured["agent_cwd"] == str(workspace)
