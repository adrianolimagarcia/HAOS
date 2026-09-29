import json
from unittest.mock import MagicMock
from hermes_cli.civ_cmd import cmd_civ


def test_civ_status(capsys, monkeypatch, tmp_path):
    # Mock managers
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    args = MagicMock()
    args.civ_action = "status"
    args.json = True

    ret = cmd_civ(args)
    assert ret == 0

    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert data["status"] == "healthy"
    assert "counts" in data
    assert "bots" in data["counts"]


def test_civ_bots_empty_and_json(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    args = MagicMock()
    args.civ_action = "bots"
    args.json = True
    args.bot_id = None

    ret = cmd_civ(args)
    assert ret == 0

    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert "bots" in data
    assert isinstance(data["bots"], list)


def test_civ_constitution_and_memory(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    # Test constitution
    args = MagicMock()
    args.civ_action = "constitution"
    args.json = True
    ret = cmd_civ(args)
    assert ret == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert "constitution" in data

    # Test memory
    args.civ_action = "memory"
    args.subject = None
    args.predicate = None
    ret = cmd_civ(args)
    assert ret == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert "assertions" in data


def test_civ_council_society_evolution(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    # Test council
    args = MagicMock()
    args.civ_action = "council"
    args.json = True
    args.council_id = None
    ret = cmd_civ(args)
    assert ret == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert "councils" in data

    # Test society
    args.civ_action = "society"
    args.domain = None
    ret = cmd_civ(args)
    assert ret == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert "reputation" in data

    # Test evolution
    args.civ_action = "evolution"
    args.bot_id = None
    ret = cmd_civ(args)
    assert ret == 0
    out, _ = capsys.readouterr()
    data = json.loads(out)
    assert "proposals" in data


def test_civ_council_deliberate_and_turns(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes.platform.observability.event_store import EventStore
    from hermes.platform.bots.manager import BotSpecManager
    from hermes.platform.bots.identity_manager import IdentityManager
    from hermes.platform.bots.spec import BotSpec
    from hermes.platform.bots.identity import BotIdentityBundle
    from hermes.platform.council.manager import CouncilManager
    from hermes.platform.council.spec import CouncilSpec

    store = EventStore(tmp_path / "events.db")
    bot_mgr = BotSpecManager(store)
    id_mgr = IdentityManager(store)
    c_mgr = CouncilManager(store)

    bot_mgr.register(BotSpec(id="arch-bot", name="Arch Bot", description="architecture"))
    bot_mgr.register(BotSpec(id="sec-bot", name="Sec Bot", description="security"))
    id_mgr.create_version("arch-bot", BotIdentityBundle(bot_id="arch-bot", soul="S1", values="V1"), activate=True)
    id_mgr.create_version("sec-bot", BotIdentityBundle(bot_id="sec-bot", soul="S2", values="V2"), activate=True)
    c_mgr.register(CouncilSpec(id="c-test", purpose="Test Purpose", members=["arch-bot", "sec-bot"]))

    # 1. Deliberate
    args = MagicMock()
    args.civ_action = "council-deliberate"
    args.council_id = "c-test"
    args.objective = "Urgent migration to distributed cluster"
    args.max_rounds = 2
    args.timeout = 10.0
    args.command_id = "cmd-op-1"
    args.json = True

    ret = cmd_civ(args)
    assert ret == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["status"] in ("completed", "approved")
    sess_id = res["session_id"]

    # 2. Debate turns
    args.civ_action = "council-debate-turns"
    args.session_id = sess_id
    args.json = True
    ret = cmd_civ(args)
    assert ret == 0
    out, _ = capsys.readouterr()
    turns_data = json.loads(out)
    assert turns_data["session_id"] == sess_id
    assert len(turns_data["debate_turns"]) >= 2


def test_civ_council_memory_project(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes.platform.observability.event_store import EventStore
    from hermes.platform.council.manager import CouncilManager
    from hermes.platform.council.spec import CouncilSpec

    store = EventStore(tmp_path / "events.db")
    c_mgr = CouncilManager(store)
    c_mgr.register(CouncilSpec(id="c-mem", purpose="Memory Test Council", members=["b1", "b2"]))

    args = MagicMock()
    args.civ_action = "council-memory-project"
    args.council_id = None
    args.rebuild = False
    args.json = True

    ret = cmd_civ(args)
    assert ret == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert "c-mem" in res


def test_civ_curator_run(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes.platform.observability.event_store import EventStore
    from hermes.platform.bots.manager import BotSpecManager
    from hermes.platform.bots.identity_manager import IdentityManager
    from hermes.platform.bots.spec import BotSpec
    from hermes.platform.bots.identity import BotIdentityBundle
    from hermes.platform.evolution.bot_evolution import BotEvolutionManager

    store = EventStore(tmp_path / "events.db")
    bot_mgr = BotSpecManager(store)
    id_mgr = IdentityManager(store)
    evo_mgr = BotEvolutionManager(store)
    bot_mgr.register(BotSpec(id="cur-bot", name="Curator Bot", description="infra"))
    id_mgr.create_version("cur-bot", BotIdentityBundle(bot_id="cur-bot", soul="Curator SOUL", values="Precision"), activate=True)

    evo_mgr.record_experience(
        bot_id="cur-bot",
        event_type="task_failure",
        domain="infra",
        summary="Lock timeout on cache",
        success=False,
    )
    evo_mgr.record_experience(
        bot_id="cur-bot",
        event_type="task_failure",
        domain="infra",
        summary="Secondary lock timeout on cache",
        success=False,
    )

    args = MagicMock()
    args.civ_action = "curator-run"
    args.dry_run = False
    args.json = True

    ret = cmd_civ(args)
    assert ret == 0
    out, _ = capsys.readouterr()
    res = json.loads(out)
    assert res["proposals_generated"] >= 1


def test_civ_graph_cli(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    args = MagicMock()
    args.civ_action = "graph"
    args.json = True

    ret = cmd_civ(args)
    assert ret == 0
    out, _ = capsys.readouterr()
    graph = json.loads(out)
    assert "nodes" in graph
    assert "edges" in graph
    assert "counts" in graph

