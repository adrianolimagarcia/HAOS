import sqlite3
import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_flow import (
    DeltaContextManager,
    FlowGraph,
    FlowNode,
    build_moa_flow,
    create_kanban_flow,
    parse_flow_dict,
    parse_flow_dsl,
    synthesize_moa_judge_verdict,
)


def test_parse_flow_dsl_linear_and_parallel():
    # Linear flow
    g1 = parse_flow_dsl("scout -> analyzer -> reporter")
    assert list(g1.nodes.keys()) == ["scout", "analyzer", "reporter"]
    assert len(g1.edges) == 2
    assert g1.validate_dag() == ["scout", "analyzer", "reporter"]
    assert g1.entry_nodes() == ["scout"]
    assert g1.terminal_nodes() == ["reporter"]

    # Fan-out / Fan-in (AgentRearrange style)
    expr = "scout:researcher:Map -> (sec:sec-expert:Security, perf:perf-expert:Perf) -> judge:conductor:Decide"
    g2 = parse_flow_dsl(expr, goal="Perform critical audit", name="audit_pipeline")
    assert set(g2.nodes.keys()) == {"scout", "sec", "perf", "judge"}
    assert g2.nodes["sec"].profile == "sec-expert"
    assert g2.nodes["sec"].title == "Security"
    assert g2.nodes["judge"].profile == "conductor"

    parents = g2.parent_map()
    children = g2.child_map()
    assert parents["sec"] == ["scout"]
    assert parents["perf"] == ["scout"]
    assert set(parents["judge"]) == {"sec", "perf"}
    assert set(children["scout"]) == {"sec", "perf"}
    assert children["judge"] == []


def test_parse_flow_dsl_cycle_rejection():
    graph = FlowGraph(name="cyclic", goal="test")
    graph.add_node(FlowNode(id="a", profile="p", title="A"))
    graph.add_node(FlowNode(id="b", profile="p", title="B"))
    graph.add_edge("a", "b")
    graph.add_edge("b", "a")

    with pytest.raises(ValueError, match="contains a cycle"):
        graph.validate_dag()


def test_parse_flow_dsl_invalid_syntax():
    with pytest.raises(ValueError, match="cannot be empty"):
        parse_flow_dsl("")

    with pytest.raises(ValueError, match="Invalid node ID format"):
        parse_flow_dsl("node@bad -> next")

    with pytest.raises(ValueError, match="Empty parallel group"):
        parse_flow_dsl("a -> () -> b")


def test_parse_flow_dict():
    data = {
        "name": "rich_flow",
        "goal": "Verify system",
        "nodes": {
            "n1": {"profile": "agent1", "title": "Step 1", "skills": ["s1"]},
            "n2": {"profile": "agent2", "title": "Step 2"},
            "n3": {"profile": "agent3", "title": "Step 3"},
        },
        "edges": [
            {"source": "n1", "target": "n2"},
            {"source": "n2", "target": "n3"},
        ],
    }
    graph = parse_flow_dict(data)
    assert graph.validate_dag() == ["n1", "n2", "n3"]
    assert graph.nodes["n1"].skills == ["s1"]


def test_delta_context_formatting():
    parent_results = {
        "scout": {
            "status": "completed",
            "confidence": 0.95,
            "summary": "Attack surface mapped. Found 1 exposed API.",
            "artifacts": ["/tmp/scout_report.json"],
            "findings": [
                {"severity": "high", "location": "api/v1/auth", "issue": "Missing auth token check"}
            ],
            "blockers": ["Network access restricted to test sandbox"],
        }
    }
    delta_text = DeltaContextManager.format_predecessor_delta(parent_results)
    assert "Delta Context from Predecessors" in delta_text
    assert "Node 'scout' [completed, confidence: 0.95]" in delta_text
    assert "Attack surface mapped" in delta_text
    assert "`/tmp/scout_report.json`" in delta_text
    assert "[HIGH] Missing auth token check" in delta_text
    assert "Network access restricted" in delta_text


def test_moa_flow_builder_and_judge_synthesis():
    graph = build_moa_flow(
        goal="Audit PR #42",
        target_description="Changes to auth.py session encryption.",
        specialists=["security", "architecture", "performance"],
    )
    assert "prepare_target" in graph.nodes
    assert "eval_security" in graph.nodes
    assert "eval_architecture" in graph.nodes
    assert "eval_performance" in graph.nodes
    assert "judge" in graph.nodes

    # Check topology: prepare -> specialists -> judge
    parents = graph.parent_map()
    assert parents["eval_security"] == ["prepare_target"]
    assert set(parents["judge"]) == {"eval_security", "eval_architecture", "eval_performance"}

    # Test Judge verdict logic: Clean reports -> Approve
    clean_reports = {
        "security": {"confidence": 0.9, "findings": [], "blockers": []},
        "architecture": {"confidence": 0.85, "findings": [], "blockers": []},
    }
    v1 = synthesize_moa_judge_verdict(clean_reports)
    assert v1["decision"] == "approve"
    assert v1["confidence"] == 0.88

    # Test Judge verdict logic: Critical finding -> Reject
    bad_reports = {
        "security": {
            "confidence": 0.95,
            "findings": [{"severity": "critical", "issue": "Remote Code Execution via deserialization"}],
            "blockers": ["Exploitable unauthenticated endpoint"],
        },
        "performance": {"confidence": 0.8, "findings": []},
    }
    v2 = synthesize_moa_judge_verdict(bad_reports)
    assert v2["decision"] == "reject"
    assert len(v2["blockers"]) >= 1


def test_create_kanban_flow_atomic_graph(tmp_path):
    conn = kbc.connect(tmp_path / "kanban.db")
    try:
        expr = "scout -> (auditor_a, auditor_b) -> synthesizer"
        graph = parse_flow_dsl(expr, goal="Review migration scripts", name="migration_review")

        created = create_kanban_flow(
            conn,
            graph=graph,
            created_by="flow-admin",
            tenant="prod",
            priority=10,
        )

        assert created.flow_name == "migration_review"
        assert set(created.task_mapping.keys()) == {"scout", "auditor_a", "auditor_b", "synthesizer"}

        root = kb.get_task(conn, created.root_id)
        scout_task = kb.get_task(conn, created.task_mapping["scout"])
        aud_a_task = kb.get_task(conn, created.task_mapping["auditor_a"])
        aud_b_task = kb.get_task(conn, created.task_mapping["auditor_b"])
        synth_task = kb.get_task(conn, created.task_mapping["synthesizer"])

        assert root is not None
        assert root.status == "done"

        # Scout has no predecessors, depends only on root -> becomes ready
        assert scout_task is not None
        assert scout_task.status == "ready"
        assert kb.parent_ids(conn, created.task_mapping["scout"]) == [created.root_id]

        # Auditors depend on scout (which is not yet done) -> status todo
        assert aud_a_task is not None
        assert aud_a_task.status == "todo"
        assert kb.parent_ids(conn, created.task_mapping["auditor_a"]) == [scout_task.id]

        assert aud_b_task is not None
        assert aud_b_task.status == "todo"
        assert kb.parent_ids(conn, created.task_mapping["auditor_b"]) == [scout_task.id]

        # Synthesizer depends on both auditors
        assert synth_task is not None
        assert synth_task.status == "todo"
        assert set(kb.parent_ids(conn, created.task_mapping["synthesizer"])) == {aud_a_task.id, aud_b_task.id}

        # Simulate completion of scout: auditors should become ready
        kb.complete_task(conn, scout_task.id, summary="Scout analysis completed successfully")
        kb.recompute_ready(conn)

        aud_a_task = kb.get_task(conn, created.task_mapping["auditor_a"])
        aud_b_task = kb.get_task(conn, created.task_mapping["auditor_b"])
        assert aud_a_task is not None and aud_a_task.status == "ready"
        assert aud_b_task is not None and aud_b_task.status == "ready"

        # Synthesizer remains todo because auditors are not yet done
        synth_task = kb.get_task(conn, created.task_mapping["synthesizer"])
        assert synth_task is not None and synth_task.status == "todo"

        # Complete both auditors: synthesizer becomes ready
        kb.complete_task(conn, aud_a_task.id, summary="Auditor A approved")
        kb.complete_task(conn, aud_b_task.id, summary="Auditor B approved")
        kb.recompute_ready(conn)

        synth_task = kb.get_task(conn, created.task_mapping["synthesizer"])
        assert synth_task is not None and synth_task.status == "ready"

    finally:
        conn.close()


def test_kanban_flow_cli_dry_run_and_execution(capsys, monkeypatch, tmp_path):
    import argparse
    from hermes_cli import kanban as kb_cli
    from hermes_cli import kanban_parser as kp

    db_path = tmp_path / "kanban.db"
    monkeypatch.setenv("HERMES_KANBAN_DB", str(db_path))

    def parse(cmd_args: list[str]) -> argparse.Namespace:
        root = argparse.ArgumentParser()
        sub = root.add_subparsers(dest="subcommand")
        kp.build_parser(sub)
        return root.parse_args(["kanban"] + cmd_args)

    # 1. Flow dry-run
    args = parse(["flow", "scout -> (sec, perf) -> judge", "--dry-run", "--json"])
    rc = kb_cli.kanban_command(args)
    assert rc == 0
    captured = capsys.readouterr().out
    assert '"topological_order"' in captured
    assert '"sec"' in captured

    # 2. Flow real creation
    args2 = parse(["flow", "scout -> (sec, perf) -> judge", "--name", "sec_eval", "--goal", "Audit auth"])
    rc2 = kb_cli.kanban_command(args2)
    assert rc2 == 0
    out2 = capsys.readouterr().out
    assert "Flow root:" in out2
    assert "Flow name: sec_eval" in out2

    # 3. Flow-MoA dry-run and creation
    args_moa_dry = parse(["flow-moa", "auth.py", "--dry-run", "--json"])
    assert kb_cli.kanban_command(args_moa_dry) == 0
    out_moa_dry = capsys.readouterr().out
    assert '"eval_security"' in out_moa_dry
    assert '"judge"' in out_moa_dry

    args_moa = parse(["flow-moa", "auth.py", "--specialist", "security", "--specialist", "architecture"])
    assert kb_cli.kanban_command(args_moa) == 0
    out_moa = capsys.readouterr().out
    assert "MoA Flow root:" in out_moa

