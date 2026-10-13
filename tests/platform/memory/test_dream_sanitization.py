import pytest
from hermes.platform.memory.dream import _sanitize_session_preview, DreamConsolidator
from hermes.platform.memory.reconciler import MemoryReconciler


def test_sanitize_session_preview_removes_system_noise():
    noisy_input = (
        "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted.\n"
        "## Historical Task Snapshot\n"
        "User asked: 'faça algo'\n\n"
        "[A2A inbound — message from a remote agent peer named 'cachyos']\n"
        "[IMPORTANT: The following skill(s) were listed for this job: foo]\n"
        "[Workspace::v1: /root/project]\n"
        "decisão de arquitetura: padronizado o uso de btrfs e swapfile\n"
        "[Observation masked: execute_code(`...`)]\n"
    )
    cleaned = _sanitize_session_preview(noisy_input)
    assert "[CONTEXT COMPACTION" not in cleaned
    assert "[A2A inbound" not in cleaned
    assert "[IMPORTANT:" not in cleaned
    assert "[Workspace::" not in cleaned
    assert "[Observation masked" not in cleaned
    assert "decisão de arquitetura: padronizado o uso de btrfs e swapfile" in cleaned


def test_dream_reconciles_expanded_technical_keywords(tmp_path):
    rec_db = tmp_path / "rec.db"
    reconciler = MemoryReconciler(db_path=rec_db)
    
    # Test new semantic technical keywords
    res = reconciler.reconcile(
        topic="kernel",
        content="padronizado kernel msm-4.4 com suporte a zstd e bfq",
        category="session_fact",
        metadata={"source": "dream_test"},
    )
    assert res.action == "ADD"
    
    active = reconciler.get_active_memories(topic="kernel")
    assert len(active) == 1
    assert "msm-4.4" in active[0].content
