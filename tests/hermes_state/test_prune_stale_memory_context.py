"""prune_stale_memory_context: cascade cleanup of durable <memory-context> fences.

The memory-context block is stamped into every user row's api_content and replayed on
each later turn (byte-stable sidecar, #6757). Only the LIVE turn's exact bytes must
survive; older rows' copies are stale duplicates that compound the prompt every turn
(measured: 75.5% of api_content bytes were these fences). These tests pin the
row-guarded, block-guarded, bounded prune.
"""

from __future__ import annotations

import pytest

from hermes_state import SessionDB

BLOCK_A = "q1\n\n<memory-context>\nMEM-A\n</memory-context>"
BLOCK_B = "q2\n\n<memory-context>\nMEM-B\n</memory-context>"


@pytest.fixture
def db(tmp_path):
    database = SessionDB(db_path=tmp_path / "state.db")
    database.create_session("s1", source="cli")
    yield database
    database.close()


def _rows(database, session="s1"):
    return {row["content"]: row for row in database.get_messages(session)}


def test_prune_strips_older_block_rows_and_keeps_live_row(db):
    db.append_message("s1", "user", content="q1", api_content=BLOCK_A)
    db.append_message("s1", "assistant", content="a1")
    keep = db.append_message("s1", "user", content="q2", api_content=BLOCK_B)

    assert db.prune_stale_memory_context("s1", keep_row_id=keep) == 1

    rows = _rows(db)
    # strip == content → NULL (no sidecar needed for byte-identical replay)
    assert rows["q1"]["api_content"] is None
    # the live row keeps its exact bytes
    assert rows["q2"]["api_content"] == BLOCK_B


def test_prune_keeps_extra_payload_after_block_strip(db):
    db.append_message("s1", "user", content="q1", api_content=BLOCK_A + "\n\nEXTRA")
    db.append_message("s1", "assistant", content="a1")
    keep = db.append_message("s1", "user", content="q2")

    assert db.prune_stale_memory_context("s1", keep_row_id=keep) == 1
    assert _rows(db)["q1"]["api_content"] == "q1\n\nEXTRA"


def test_prune_without_keep_targets_newest_user_row(db):
    db.append_message("s1", "user", content="q1", api_content=BLOCK_A)
    db.append_message("s1", "assistant", content="a1")
    db.append_message("s1", "user", content="q2", api_content=BLOCK_B)

    assert db.prune_stale_memory_context("s1") == 1

    rows = _rows(db)
    assert rows["q1"]["api_content"] is None
    assert rows["q2"]["api_content"] == BLOCK_B


def test_prune_leaves_rows_without_blocks_untouched(db):
    db.append_message("s1", "user", content="q1", api_content="q1\n\nPLUGIN-CTX")
    db.append_message("s1", "assistant", content="a1")
    keep = db.append_message("s1", "user", content="q2")

    assert db.prune_stale_memory_context("s1", keep_row_id=keep) == 0
    assert _rows(db)["q1"]["api_content"] == "q1\n\nPLUGIN-CTX"


def test_prune_is_bounded_by_row_limit(db):
    for index in range(240):
        db.append_message("s1", "user", content=f"q{index}", api_content=f"q{index}\n\n<memory-context>\nM{index}\n</memory-context>")
    keep = db.append_message("s1", "user", content="live")

    # 240 fenced candidates → walk limit 200 prunes exactly 200, 40 leftovers remain
    assert db.prune_stale_memory_context("s1", keep_row_id=keep, limit=200) == 200
    rows = [row for row in db.get_messages("s1") if row["api_content"] and "<memory-context>" in row["api_content"]]
    assert len(rows) == 40


def test_prune_unknown_session_is_noop(db):
    assert db.prune_stale_memory_context("nope", keep_row_id=1) == 0
