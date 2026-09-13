"""Tests for HAOS Code Knowledge Graph & Graphify-inspired engine."""

import argparse
import json
from pathlib import Path

from hermes.platform.capabilities.lsp.unified_intelligence import CodeSymbolGraph
from hermes_cli.haos_cmd import cmd_haos_graph_build, cmd_haos_graph_path


def test_code_symbol_graph_scan_and_path(tmp_path: Path):
    # Setup 3 chained files: A -> B -> C
    file_c = tmp_path / "service_c.py"
    file_c.write_text("def worker_c():\n    return 42\n", encoding="utf-8")

    file_b = tmp_path / "service_b.py"
    file_b.write_text("def intermediate_b():\n    return worker_c()\n", encoding="utf-8")

    file_a = tmp_path / "entry_a.py"
    file_a.write_text("def start_a():\n    intermediate_b()\n", encoding="utf-8")

    graph = CodeSymbolGraph()
    count = graph.scan_directory(str(tmp_path))
    assert count >= 3

    # Check BFS path finding
    path = graph.find_path("start_a", "worker_c")
    assert path is not None
    assert len(path) == 3
    assert "start_a" in path[0]
    assert "intermediate_b" in path[1]
    assert path[2] == "worker_c"


def test_identify_god_components(tmp_path: Path):
    god_file = tmp_path / "god_service.py"
    god_file.write_text(
        "def m1():\n    pass\ndef m2():\n    m1()\ndef m3():\n    m1()\ndef m4():\n    m1()\n",
        encoding="utf-8",
    )
    graph = CodeSymbolGraph()
    graph.scan_directory(str(tmp_path))

    gods = graph.identify_god_components(top_k=3)
    assert len(gods) >= 1
    assert gods[0]["file_path"] == "god_service.py"
    assert gods[0]["coupling_score"] > 0


def test_export_graph_report(tmp_path: Path):
    src_file = tmp_path / "app.py"
    src_file.write_text("class AppRunner:\n    def run(self):\n        pass\n", encoding="utf-8")

    graph = CodeSymbolGraph()
    graph.scan_directory(str(tmp_path))

    out_dir = tmp_path / "out"
    artifacts = graph.export_graph_report(str(out_dir))

    json_path = Path(artifacts["graph_json"])
    md_path = Path(artifacts["graph_report"])

    assert json_path.exists()
    assert md_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["summary"]["nodes_count"] >= 2
    assert "summary" in payload

    report_text = md_path.read_text(encoding="utf-8")
    assert "HAOS Code Knowledge Graph Report" in report_text
    assert "God Components" in report_text


def test_cli_graph_commands(tmp_path: Path, capsys):
    f1 = tmp_path / "main.py"
    f1.write_text("def run():\n    helper()\n", encoding="utf-8")

    build_args = argparse.Namespace(dir=str(tmp_path), out=str(tmp_path / "graphify-out"))
    exit_code = cmd_haos_graph_build(build_args)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "HAOS CODE KNOWLEDGE GRAPH" in captured.out
    assert "Símbolos indexados" in captured.out

    path_args = argparse.Namespace(
        source="run", target="helper", dir=str(tmp_path)
    )
    exit_code = cmd_haos_graph_path(path_args)
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "PATH TRACER" in captured.out
    assert "Trajetória encontrada" in captured.out
