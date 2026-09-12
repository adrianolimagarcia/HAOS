"""Regression tests for ``MCPServerTask.run`` + ``asyncio.CancelledError``.

Background
==========
On Python 3.11+, ``asyncio.CancelledError`` inherits from ``BaseException``
rather than ``Exception``, so a bare ``except Exception`` does NOT catch it.
``MCPServerTask.run`` had a broad ``except Exception`` around the transport
loop which meant a task cancellation (gateway restart, explicit
``task.cancel()``) caused the reconnect loop to exit silently — the MCP
server stayed dead until Hermes was restarted. See #9930.

The fix adds an explicit ``except asyncio.CancelledError: raise`` BEFORE
the broad catch so cancellation propagates cleanly to asyncio's task
machinery and ``MCPServerTask.shutdown()``'s ``await self._task`` completes
without hanging the reconnect loop.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest



async def _hanging_run(self, cfg):
    """Stand-in transport that hangs forever so we can cancel it."""
    await asyncio.sleep(3600)


class TestCancelledErrorPropagation:
    def test_cancelled_error_is_not_swallowed_by_except_exception(self):
        """CancelledError raised inside the transport call must re-raise
        so the reconnect loop terminates cleanly on cancel — not stay wedged."""
        from tools.mcp_tool import MCPServerTask

        server = MCPServerTask("cancel-test")

        async def drive():
            with patch.object(MCPServerTask, "_run_stdio", _hanging_run), \
                 patch.object(MCPServerTask, "_is_http", lambda self: False):
                task = asyncio.create_task(server.run({"command": "fake"}))
                # Let the run loop enter the try/except and start awaiting.
                await asyncio.sleep(0.05)
                task.cancel()
                # The fix guarantees the task completes (either via
                # CancelledError propagation or clean exit) rather than
                # hanging forever.
                try:
                    await asyncio.wait_for(task, timeout=15.0)
                except asyncio.CancelledError:
                    return "cancelled_cleanly"
                except asyncio.TimeoutError:
                    # If we hit this, the reconnect loop swallowed the cancel
                    # and stayed wedged — the exact #9930 bug.
                    task.cancel()
                    try:
                        await task
                    except Exception:
                        pass
                    return "wedged"
                return "clean_return"

        outcome = asyncio.run(drive())
        assert outcome in {"cancelled_cleanly", "clean_return"}, (
            f"MCPServerTask.run wedged on cancel (outcome={outcome}) — "
            f"#9930 regression"
        )

    def test_shutdown_completes_promptly_when_task_is_cancelled(self):
        """``shutdown()`` falls through to ``task.cancel()`` + ``await self._task``
        after a grace period. That cancel must unwedge the reconnect loop —
        otherwise ``await self._task`` hangs indefinitely."""
        from tools.mcp_tool import MCPServerTask

        server = MCPServerTask("shutdown-cancel-test")

        async def drive():
            with patch.object(MCPServerTask, "_run_stdio", _hanging_run), \
                 patch.object(MCPServerTask, "_is_http", lambda self: False):
                server._task = asyncio.ensure_future(server.run({"command": "fake"}))
                await asyncio.sleep(0.05)
                server._shutdown_event.set()
                server._task.cancel()
                try:
                    await asyncio.wait_for(server._task, timeout=15.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                return server._task.done()

        done = asyncio.run(drive())
        assert done, "MCPServerTask did not finish after cancel — #9930 regression"

    @pytest.mark.asyncio
    async def test_run_terminates_when_cancelled_during_lifecycle_reap(self, monkeypatch):
        """A stop request that lands while ``run()`` reaps its lifecycle-event waiters must
        end the loop.

        Same "swallowed cancellation" defect class as the Buzz websocket (fixed in
        commit 47d92cc6eb): ``_cancel_waiters`` reaped each waiter with ``task.cancel();
        try: await task / except (asyncio.CancelledError, Exception): pass``. When the run
        task's OWN cancel arrives while it is suspended inside that reap, ``Task.cancel()``
        cancels the waiter (the child) and returns WITHOUT setting ``_must_cancel``; the
        CancelledError born at the await point is then swallowed by the except. The
        ``while True`` loop in ``run()`` reconnects and the stop request is lost — an
        unbounded ``await task`` (``shutdown()``'s post-timeout fallback, or a test join)
        hangs forever.

        The shutdown-waiter's cancellation is deliberately not instantaneous so the cancel
        lands inside the reap window deterministically; the reconnect-waiter completes
        immediately so the lifecycle wait breaks and actually enters the reap.
        """
        from tools.mcp_tool import MCPServerTask

        server = MCPServerTask("reap-cancel-test")
        reap_started = asyncio.Event()

        async def slow_shutdown_wait():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                reap_started.set()
                await asyncio.sleep(0.3)
                raise

        async def fast_reconnect_wait():
            await asyncio.sleep(0.01)

        def fake_event_waiters():
            return (asyncio.create_task(slow_shutdown_wait()),
                    asyncio.create_task(fast_reconnect_wait()))

        async def fake_transport(self, config):
            # Real lifecycle wait (and its reap) instead of a real stdio/HTTP run.
            return await server._wait_for_lifecycle_event()

        monkeypatch.setattr(server, "_event_waiters", fake_event_waiters)
        monkeypatch.setattr(MCPServerTask, "_run_stdio", fake_transport)
        monkeypatch.setattr(MCPServerTask, "_is_http", lambda self: False)

        task = asyncio.create_task(server.run({"command": "fake", "keepalive_interval": 5}))
        try:
            # The run loop is now suspended inside the reap of the lifecycle waiters.
            await asyncio.wait_for(reap_started.wait(), 5.0)
            task.cancel()
            _done, pending = await asyncio.wait({task}, timeout=2.0)
            assert not pending, (
                "MCPServerTask.run ignored a cancellation that landed during the "
                "lifecycle-event reap and kept reconnecting — swallowed-cancel regression"
            )
        finally:
            # Kill the run task even when a swallowed cancel left it reconnecting: keep
            # cancelling (bounded, tight cadence) until it stops, so the failing assertion
            # reports instead of the pytest-asyncio Runner teardown hanging on the wedged
            # task (runner teardown runs `_cancel_all_tasks` + an unbounded gather).
            deadline = time.monotonic() + 6.0
            while not task.done() and time.monotonic() < deadline:
                task.cancel()
                await asyncio.wait({task}, timeout=0.01)
