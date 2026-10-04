"""Hot-serve invariants for ``gateway.multiplex_profiles`` (``gateway/run_profile_reconcile.py``).

The multiplexer used to enumerate ``profiles/`` once at boot; these pin the runtime reconcile: a
profile created afterwards is served, a deleted one is torn down and unrouted, a served profile whose
config/.env changed (bot token added after create) gets its adapters, and none of it touches the other
profiles' live adapters. The cron ticker's live enumerator is covered in ``tests/cron``.
"""
import asyncio
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.run import GatewayRunner
from gateway.run_profile_reconcile import profile_serve_signature


class _Adapter:
    platform = Platform.DISCORD

    def __init__(self, token):
        self.token = token
        self.disconnected = False
        self.cancelled = False

    async def disconnect(self):
        self.disconnected = True

    async def cancel_background_tasks(self):
        self.cancelled = True


def _runner(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    (home / "profiles").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner._running = True
    runner._primary_profile_name = "default"
    runner.adapters = {}
    runner._profile_adapters = {}
    runner._profile_failed_platforms = {}
    runner._failed_platforms = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = None
    runner.pairing_store = MagicMock()
    runner.pairing_stores = {}
    runner._adapter_disconnect_timeout_secs = lambda: 0.5
    started = []

    async def _start(profile_name, profile_home, claimed):
        started.append(profile_name)
        token = (profile_home / ".env").read_text(encoding="utf-8") if (profile_home / ".env").exists() else ""
        if "DISCORD_BOT_TOKEN" not in token:
            return 0
        runner._profile_adapters.setdefault(profile_name, {})[Platform.DISCORD] = _Adapter(token)
        return 1

    runner._start_one_profile_adapters = _start
    runner._adapter_credential_fingerprint = lambda adapter: getattr(adapter, "token", None)
    runner._started = started
    return runner, home


def _mkprofile(home, name, env=""):
    d = home / "profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text("model: {default: m}\n", encoding="utf-8")
    (d / ".env").write_text(env, encoding="utf-8")
    return d


def _served_record(home):
    return json.loads((home / "gateway_state.json").read_text(encoding="utf-8")).get("served_profiles")


@pytest.mark.asyncio
async def test_opt_out_rescans_and_opt_in_waits_for_own_gateway_to_stop(tmp_path, monkeypatch, caplog):
    runner, home = _runner(tmp_path, monkeypatch)
    solo = _mkprofile(home, "solo", "DISCORD_BOT_TOKEN=solo-token\n")
    own_pids = {}
    monkeypatch.setattr("gateway.status.live_gateway_pid_for_home", lambda h: own_pids.get(h))
    with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"):
        await runner._start_secondary_profile_adapters()
        adapter = runner._profile_adapters["solo"][Platform.DISCORD]
        (solo / "config.yaml").write_text("gateway:\n  standalone: true\n")
        result = await runner.reconcile_served_profiles()
        assert result["removed"] == ["solo"]
        assert adapter.disconnected
        assert _served_record(home) == ["default"]

        own_pids[solo] = 12345
        (solo / "config.yaml").write_text("gateway:\n  standalone: false\n")
        for _ in range(2):
            result = await runner.reconcile_served_profiles()
            assert result["added"] == []
            assert result["served_profiles"] == ["default"]
        assert runner._started.count("solo") == 1
        assert len([r for r in caplog.records if "still runs its own gateway" in r.message]) == 1

        own_pids.clear()
        result = await runner.reconcile_served_profiles()
        assert result["added"] == ["solo"]
        assert _served_record(home) == ["default", "solo"]
        assert runner._started.count("solo") == 2


@pytest.mark.asyncio
async def test_stalled_own_gateway_probe_never_wedges_the_loop_or_serves(tmp_path, monkeypatch, caplog):
    """#132547: the pre-serve own-gateway probe can end in a control-socket read that has no
    timeout of its own (a Windows named pipe stalls there until its peer answers); inline on
    the loop thread it parked shutdown_watchdog liveness probes until the multiplexer was
    hard-killed with exit 75. A stalled probe must leave the event loop turning, keep the
    profile unserved for that cycle, and the profile must serve once the probe answers."""
    from gateway import run_profile_reconcile as reconcile_mod

    runner, home = _runner(tmp_path, monkeypatch)
    _mkprofile(home, "alpha", "DISCORD_BOT_TOKEN=alpha-token\n")
    with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"):
        await runner._start_secondary_profile_adapters()

        released = threading.Event()

        def _stalling_probe(profile_home):
            released.wait(timeout=10)  # the control pipe that never answers
            return None

        monkeypatch.setattr("gateway.status.live_gateway_pid_for_home", _stalling_probe)
        monkeypatch.setattr(reconcile_mod, "_OWN_GATEWAY_PROBE_TIMEOUT_SECS", 0.2)

        _mkprofile(home, "gamma", "DISCORD_BOT_TOKEN=gamma-token\n")

        async def _liveness_probe():
            # shutdown_watchdog stand-in: the loop must keep turning while the probe is stalled.
            for _ in range(4):
                await asyncio.sleep(0.05)

        heartbeat = asyncio.ensure_future(_liveness_probe())
        # On the unfixed code an inline probe parks the loop for the full ``released`` wait
        # and this wait_for times out instead of returning a reconcile result.
        result = await asyncio.wait_for(runner.reconcile_served_profiles(reason="watcher"), timeout=2.0)
        assert result["added"] == []
        assert _served_record(home) == ["default", "alpha"]
        assert len([r for r in caplog.records if "still runs its own gateway" in r.message]) == 1
        await asyncio.wait_for(heartbeat, timeout=1.0)

        # A peer that stays wedged must not re-WARN on every 30 s watcher cycle.
        result = await runner.reconcile_served_profiles(reason="watcher")
        assert result["added"] == []
        stalled_warnings = [r for r in caplog.records if r.levelname == "WARNING"
                            and "probe for profile 'gamma' timed out" in r.getMessage()]
        assert len(stalled_warnings) == 1

        released.set()
        result = await runner.reconcile_served_profiles(reason="watcher")
        assert result["added"] == ["gamma"]
        assert _served_record(home) == ["default", "alpha", "gamma"]


@pytest.mark.asyncio
async def test_parked_profile_boot_and_reconcile(tmp_path, monkeypatch, caplog):
    runner, home = _runner(tmp_path, monkeypatch)
    secondary = _mkprofile(home, "worker")
    marker = secondary / "gateway.parked"
    marker.touch()
    # Boot uses the real directory enumerator and config loaders, no bot/network.
    del runner._start_one_profile_adapters
    runner._register_config_hooks = lambda *a, **kw: None
    caplog.set_level("INFO")
    with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"):
        await runner._start_secondary_profile_adapters()
        assert _served_record(home) == ["default"]
        assert "profile 'worker' is parked (gateway.parked); not served by this gateway" in caplog.text
        marker.unlink()
        assert (await runner.reconcile_served_profiles())["added"] == ["worker"]
        assert _served_record(home) == ["default", "worker"]
        marker.touch()
        assert (await runner.reconcile_served_profiles())["removed"] == ["worker"]
        assert _served_record(home) == ["default"]


@pytest.mark.asyncio
async def test_profile_control_verbs_round_trip_and_refusals(tmp_path, monkeypatch):
    from gateway import run_profile_reconcile as verbs
    runner, home = _runner(tmp_path, monkeypatch)
    secondary = _mkprofile(home, "worker", "DISCORD_BOT_TOKEN=worker-token\n")
    with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"):
        await runner._start_secondary_profile_adapters()
        stop = verbs.unserve_profile_verb(runner)
        start = verbs.serve_profile_verb(runner)
        for handler, name in [(stop, "default"), (stop, "missing"), (start, "missing"),
                              (start, "worker"), (start, "default")]:
            assert (await asyncio.to_thread(handler, {"name": name}))["error"]
        old = runner._profile_adapters["worker"][Platform.DISCORD]
        answer = await asyncio.to_thread(stop, {"name": "worker"})
        assert answer["unserved"] == "worker"
        assert answer["served_profiles"] == _served_record(home) == ["default"]
        assert old.disconnected
        marker = secondary / "gateway.parked"
        marker.touch()
        assert (await asyncio.to_thread(start, {"name": "worker"}))["error"]
        marker.unlink()
        (secondary / ".env").write_text("DISCORD_BOT_TOKEN=new-worker-token\n")
        answer = await asyncio.to_thread(start, {"name": "worker"})
        assert answer["served"] == "worker"
        assert answer["served_profiles"] == _served_record(home) == ["default", "worker"]
        assert runner._profile_adapters["worker"][Platform.DISCORD].token.endswith("new-worker-token\n")


@pytest.mark.platforms("linux")
@pytest.mark.asyncio
async def test_profile_lifecycle_over_real_control_socket(tmp_path, monkeypatch):
    from gateway.run import _start_gateway_start_control_socket
    from gateway import control_socket
    runner, home = _runner(tmp_path, monkeypatch)
    secondary = _mkprofile(home, "worker")
    with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"):
        await runner._start_secondary_profile_adapters()
        server = await _start_gateway_start_control_socket(runner)
        assert server is not None
        try:
            (secondary / "gateway.parked").touch()
            stopped = await asyncio.to_thread(control_socket.request_unserve_profile, home, "worker")
            assert stopped["unserved"] == "worker"
            assert _served_record(home) == ["default"]
            refused = await asyncio.to_thread(control_socket.request_serve_profile_hot, home, "worker")
            assert "parked" in refused["error"]
            (secondary / "gateway.parked").unlink()
            started = await asyncio.to_thread(control_socket.request_serve_profile_hot, home, "worker")
            assert started["served"] == "worker"
            assert _served_record(home) == ["default", "worker"]
        finally:
            await server.stop()


@pytest.mark.asyncio
async def test_created_then_credentialed_profile_is_served_without_restart(tmp_path, monkeypatch):
    runner, home = _runner(tmp_path, monkeypatch)
    alpha_dir = _mkprofile(home, "alpha", "DISCORD_BOT_TOKEN=alpha-token\n")
    with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"):
        await runner._start_secondary_profile_adapters()
        alpha_adapter = runner._profile_adapters["alpha"][Platform.DISCORD]
        assert _served_record(home) == ["default", "alpha"]

        # 1. Created while running, no token yet: served (routes/prefixes/cron), zero adapters.
        gamma_dir = _mkprofile(home, "gamma")
        result = await runner.reconcile_served_profiles()
        assert result["added"] == ["gamma"]
        assert _served_record(home) == ["default", "alpha", "gamma"]
        assert "gamma" in runner.pairing_stores
        assert Platform.DISCORD not in runner._profile_adapters.get("gamma", {})

        # 2. Token added afterwards: the rescan builds the adapter (never "adapter-less forever").
        (gamma_dir / ".env").write_text("DISCORD_BOT_TOKEN=gamma-token\n", encoding="utf-8")
        result = await runner.reconcile_served_profiles()
        assert result["rescanned"] == ["gamma"]
        assert runner._profile_adapters["gamma"][Platform.DISCORD].token.strip().endswith("gamma-token")

        # 3. A no-op rescan and the whole sequence never touched alpha's live adapter.
        assert await runner.reconcile_served_profiles() == {
            "added": [], "removed": [], "rescanned": [], "reason": "request",
            "served_profiles": ["default", "alpha", "gamma"],
        }
        assert runner._profile_adapters["alpha"][Platform.DISCORD] is alpha_adapter
        assert alpha_adapter.disconnected is False
        assert runner._started.count("alpha") == 1
        assert profile_serve_signature(alpha_dir) == runner._served_profile_signatures["alpha"]


@pytest.mark.asyncio
async def test_deleted_profile_is_torn_down_and_unrouted_others_untouched(tmp_path, monkeypatch):
    runner, home = _runner(tmp_path, monkeypatch)
    _mkprofile(home, "alpha", "DISCORD_BOT_TOKEN=alpha-token\n")
    gamma_dir = _mkprofile(home, "gamma", "DISCORD_BOT_TOKEN=gamma-token\n")
    with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"):
        await runner._start_secondary_profile_adapters()
        alpha_adapter = runner._profile_adapters["alpha"][Platform.DISCORD]
        gamma_adapter = runner._profile_adapters["gamma"][Platform.DISCORD]
        runner._agent_cache = {"agent:gamma:discord:dm:1": ("agent",), "agent:alpha:discord:dm:1": ("agent",)}
        evicted = []
        runner._evict_cached_agent = evicted.append
        reconnect = asyncio.get_running_loop().create_task(asyncio.sleep(3600))
        runner._profile_failed_platforms = {"gamma": {Platform.TELEGRAM: reconnect}}

        from hermes_constants import mark_named_profile_deleted
        mark_named_profile_deleted(gamma_dir)  # what ``delete_profile`` does before rmtree
        result = await runner.reconcile_served_profiles()

    assert result["removed"] == ["gamma"]
    assert gamma_adapter.disconnected is True and gamma_adapter.cancelled is True
    assert "gamma" not in runner._profile_adapters
    assert "gamma" not in runner.pairing_stores
    assert reconnect.cancelled()
    assert evicted == ["agent:gamma:discord:dm:1"]
    assert _served_record(home) == ["default", "alpha"]
    assert runner._profile_adapters["alpha"][Platform.DISCORD] is alpha_adapter
    assert alpha_adapter.disconnected is False


@pytest.mark.asyncio
async def test_hot_added_profile_cannot_double_claim_a_live_secondary_token(tmp_path, monkeypatch):
    """Boot's duplicate-credential guard sees every profile at once; a hot add must see the LIVE
    secondaries' claims too, or the new profile starts a second poller on alpha's bot."""
    runner, home = _runner(tmp_path, monkeypatch)
    _mkprofile(home, "alpha", "DISCORD_BOT_TOKEN=shared\n")
    seen_claims = {}

    async def _start(profile_name, profile_home, claimed):
        seen_claims[profile_name] = dict(claimed)
        runner._profile_adapters.setdefault(profile_name, {})[Platform.DISCORD] = _Adapter("shared")
        return 1

    runner._start_one_profile_adapters = _start
    with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"):
        await runner._start_secondary_profile_adapters()
        _mkprofile(home, "dupe", "DISCORD_BOT_TOKEN=shared\n")
        await runner.reconcile_served_profiles()
    fp = GatewayRunner._adapter_credential_fingerprint(_Adapter("shared"))
    assert seen_claims["dupe"].get((Platform.DISCORD, fp)) == "alpha"
