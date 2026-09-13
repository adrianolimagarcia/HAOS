"""Contract tests for HAOS Dream Routine (consolidation + git audit store)."""

from pathlib import Path
import pytest

from hermes.platform.memory.dream import DreamConsolidator, DreamGitStore
from hermes_state import SessionDB


def test_dream_git_store_init_and_commit(tmp_path: Path):
    memory_root = tmp_path / "memory"
    store = DreamGitStore(memory_root)

    # Init
    created = store.init_if_needed()
    assert created is True
    assert (memory_root / ".git").is_dir()

    # Commit when clean should return None
    assert store.commit_changes() is None

    # Write a new memory file and commit
    test_file = memory_root / "test_memory.md"
    test_file.write_text("# Knowledge\nSome facts learned.\n", encoding="utf-8")

    info = store.commit_changes("dream: add test memory")
    assert info is not None
    assert len(info.sha) >= 7
    assert "dream: add test memory" in info.message

    # List commits
    commits = store.list_commits()
    assert len(commits) >= 2
    assert any("dream: add test memory" in c["subject"] for c in commits)


def test_dream_consolidator_runs_and_records_cursor(tmp_path: Path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Seed state.db with a completed session
    db = SessionDB()
    sid = "20260908_test_dream_session"
    db.ensure_session(
        session_id=sid,
        source="cli",
        model="gemini-test",
    )
    db.set_session_title(sid, "Operação HAOS Dream")
    db.append_message(sid, "user", "Qual o plano de contingência?")
    db.append_message(sid, "assistant", "O plano de contingência é usar rotas secundárias locais.")
    db.close()

    consolidator = DreamConsolidator(hermes_home=home)
    assert consolidator.get_cursor() == 0.0

    # Dry-run
    dry_res = consolidator.run_dream(dry_run=True)
    assert dry_res["status"] == "dry_run"
    assert dry_res["consolidated_count"] == 1
    assert consolidator.get_cursor() == 0.0

    # Real run
    res = consolidator.run_dream(dry_run=False)
    assert res["status"] == "success"
    assert res["consolidated_count"] == 1
    assert res["commit"] is not None
    assert consolidator.get_cursor() > 0.0

    # Second run without new sessions should be idle
    idle_res = consolidator.run_dream(dry_run=False)
    assert idle_res["status"] == "idle"
    assert idle_res["consolidated_count"] == 0
