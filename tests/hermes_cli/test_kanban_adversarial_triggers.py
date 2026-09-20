"""Adversarial contracts for concurrent claims, stale workers, and board isolation."""
from __future__ import annotations
import concurrent.futures
import time
from pathlib import Path
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

def _home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"; home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home)); monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear(); kb.init_db(); return home

def test_concurrent_claims_have_single_winner(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    with kbc.connect() as conn: task_id = kb.create_task(conn, title="one", assignee="worker", initial_status="running")
    def claim():
        with kbc.connect() as conn:
            task = kb.claim_task(conn, task_id, claimer="race"); return None if task is None else task.id
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(lambda _: claim(), range(2)))
    assert results.count(task_id) == 1; assert results.count(None) == 1

def test_stale_claim_reclaim_allows_new_owner(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    with kbc.connect() as conn:
        task_id = kb.create_task(conn, title="stale", assignee="worker", initial_status="running")
        assert kb.claim_task(conn, task_id, ttl_seconds=1, claimer="old") is not None
        conn.execute("UPDATE tasks SET claim_expires = ? WHERE id = ?", (int(time.time()) - 1, task_id)); conn.commit()
        assert kb.claim_task(conn, task_id, claimer="new") is None
        assert kb.release_stale_claims(conn, signal_fn=lambda *_a, **_k: None) == 1
        assert kb.claim_task(conn, task_id, claimer="new") is not None
        assert kb.get_task(conn, task_id).claim_lock == "new"

def test_board_isolation_prevents_cross_board_claims(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    with kbc.connect(board="alpha") as alpha, kbc.connect(board="beta") as beta:
        alpha_id = kb.create_task(alpha, title="alpha", assignee="a", board="alpha", initial_status="running")
        assert kb.get_task(beta, alpha_id) is None
        beta_id = kb.create_task(beta, title="beta", assignee="b", board="beta", initial_status="running")
        assert kb.claim_task(alpha, beta_id, claimer="wrong") is None
        assert kb.claim_task(beta, beta_id, claimer="right") is not None
