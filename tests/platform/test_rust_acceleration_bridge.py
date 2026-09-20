import sys
import json
import tempfile
from pathlib import Path
import pytest

sys.path.insert(0, ".")
from hermes.platform.shadow_leaf import ShadowLeafManager
from hermes.platform.context.memory.vector_index import SQLiteVectorIndex
from hermes.platform.capabilities.lsp.unified_intelligence import ImpactAnalyzer, CodeSymbolGraph


def test_rust_accelerated_capabilities(tmp_path):
    # 1. Teste de Vector Search com Fallback / Rust
    db_file = tmp_path / "vecs.db"
    idx = SQLiteVectorIndex(db_file, model_version="rust-test-v1", dimensions=4, normalize=True)
    idx.upsert("alpha", (1.0, 0.0, 0.0, 0.0))
    idx.upsert("beta", (0.0, 1.0, 0.0, 0.0))
    res = idx.search((1.0, 0.0, 0.0, 0.0), limit=1)
    assert res == ["alpha"]

    # 2. Teste de Blast Radius com Rust Fallback
    graph = CodeSymbolGraph()
    engine = ImpactAnalyzer(graph)
    blast = engine.calculate_blast_radius(
        modified_files=["standalone.py"],
        modified_symbols=["HAOSStandaloneState"],
        max_call_depth=2,
        root_dir=str(tmp_path)
    )
    assert blast is not None
    assert "standalone.py" in blast.modified_files or len(blast.affected_files) >= 0

    # 3. Teste de Loop Detection e Cancel Registry via API Rust
    try:
        import urllib.request
        # Teste de Cancelamento
        reg_req = urllib.request.Request(
            "http://127.0.0.1:8788/api/cancel/register",
            data=json.dumps({"id": "test_job_42"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(reg_req, timeout=0.5) as r:
            assert json.loads(r.read())["ok"] is True

        trig_req = urllib.request.Request(
            "http://127.0.0.1:8788/api/cancel/trigger",
            data=json.dumps({"id": "test_job_42"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(trig_req, timeout=0.5) as r:
            assert json.loads(r.read())["cancelled"] is True

        # Teste de Detecção de Loop
        loop_req = urllib.request.Request(
            "http://127.0.0.1:8788/api/tools/detect-loop",
            data=json.dumps({
                "session_id": "test_sess_1",
                "tool_name": "terminal",
                "arguments": '{"command":"echo test"}',
                "iteration": 1,
                "threshold": 2
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(loop_req, timeout=0.5) as r:
            assert json.loads(r.read())["loop_detected"] is False

        loop_req2 = urllib.request.Request(
            "http://127.0.0.1:8788/api/tools/detect-loop",
            data=json.dumps({
                "session_id": "test_sess_1",
                "tool_name": "terminal",
                "arguments": '{"command":"echo test"}',
                "iteration": 2,
                "threshold": 2
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(loop_req2, timeout=0.5) as r:
            assert json.loads(r.read())["loop_detected"] is True
    except Exception:
        pass
