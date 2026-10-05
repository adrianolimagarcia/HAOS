"""End-to-end ADR-022 projection contracts (isolated per-profile SQLite)."""
import concurrent.futures
import hashlib
import json
import sqlite3

import pytest

from hermes.platform.context.memory.mental_model_client import MentalModelClient
from hermes.platform.memory.dream import DreamConsolidator
from hermes_state import SessionDB


LESSON = 'Reusable workflow procedure to deploy with rollback'


def _session(home, number):
    sid = f'20261004_dream_model_{number:02d}'
    db = SessionDB(db_path=home / 'state.db')
    try:
        db.ensure_session(session_id=sid, source='cli', model='test')
        db.append_message(sid, 'user', LESSON)
        db.append_message(sid, 'assistant', 'Confirmed.')
    finally:
        db.close()
    return sid


def _projection():
    return [
        {'type': 'add_section', 'section_id': 's', 'title': 'Rules', 'order': 0},
        {'type': 'append_block', 'section_id': 's', 'block': {
            'block_id': 'b', 'content': 'Use rollback', 'proof_count': 2,
            'source_node_ids': ['session://a', 'session://b']}},
    ]


def test_client_transaction_isolation_and_idempotence(tmp_path):
    home = tmp_path / 'profile'
    other = tmp_path / 'other'
    client = MentalModelClient(home, native_path=tmp_path / 'missing.so')
    with pytest.raises(ValueError, match='Section not found'):
        client.apply_deltas('rules', [_projection()[0], {'type': 'append_block', 'section_id': 'unknown', 'block': _projection()[1]['block']}])
    with sqlite3.connect(client.db_path) as db:
        assert db.execute('SELECT count(*) FROM haos_mental_models').fetchone()[0] == 0
    assert client.apply_deltas('rules', _projection()) is True
    assert client.apply_deltas('rules', _projection()) is False
    markdown = client.get_compiled_markdown('rules')
    assert markdown is not None and 'Use rollback' in markdown
    assert MentalModelClient(other).get_compiled_markdown('rules') is None
    with sqlite3.connect(client.db_path) as db:
        version, raw = db.execute('SELECT version, ast_json FROM haos_mental_models').fetchone()
        assert version == 2
        assert json.loads(raw)['sections'][0]['blocks'][0]['proof_count'] == 2
    assert not (home / 'state.db').exists()
    assert not (other / 'state.db').exists()
    with pytest.raises(RuntimeError, match='Native TEMPR is unavailable'):
        client.tempr_search('rollback')


def test_concurrent_replay_is_one_write(tmp_path):
    client = MentalModelClient(tmp_path / 'profile')
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.apply_deltas('rules', _projection()), range(16)))
    assert results.count(True) == 1
    assert results.count(False) == 15
    with sqlite3.connect(client.db_path) as db:
        assert db.execute('SELECT version FROM haos_mental_models').fetchone()[0] == 2


def test_dream_projects_promoted_fact_and_reports_only_actual_changes(tmp_path, monkeypatch):
    home = tmp_path / 'profile'
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(foreign))
    monkeypatch.setenv('HAOS_HOME', str(foreign))
    dream = DreamConsolidator(hermes_home=home)
    for i in range(1, 5):
        sid = _session(home, i)
        result = dream.run_dream()
        assert result['mental_models_updated'] == (1 if i == 4 else 0)
        assert result['promoted_count'] == (1 if i == 4 else 0)
    client = dream.mental_model_client
    assert client.db_path == home / 'memory' / 'raggraph.db'
    with sqlite3.connect(client.db_path) as db:
        raw = db.execute('SELECT ast_json FROM haos_mental_models WHERE model_id=?', ('core_directives',)).fetchone()[0]
    block = json.loads(raw)['sections'][0]['blocks'][0]
    assert block['block_id'] == hashlib.sha256(LESSON.encode()).hexdigest()
    assert block['proof_count'] == 4
    assert block['source_node_ids'] == [f'session://20261004_dream_model_{i:02d}' for i in range(1, 5)]
    assert LESSON in client.get_compiled_markdown('core_directives')
    assert not (foreign / 'memory' / 'raggraph.db').exists()
    _session(home, 5)
    replay = dream.run_dream()
    assert replay['mental_models_updated'] == 0
    assert replay['promoted_count'] == 0
    assert replay['consolidated_count'] == 0


def test_failed_projection_is_retried_without_repromotion(tmp_path, monkeypatch):
    home = tmp_path / 'profile'
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HAOS_HOME', str(home))
    dream = DreamConsolidator(hermes_home=home)
    for i in range(1, 4):
        _session(home, i)
        dream.run_dream()
    original = dream.mental_model_client.apply_deltas
    def fail(*args):
        raise OSError('simulated projection outage')
    dream.mental_model_client.apply_deltas = fail
    _session(home, 4)
    failed = dream.run_dream()
    assert failed['promoted_count'] == 1
    assert failed['mental_models_updated'] == 0
    assert dream.mental_model_client.get_compiled_markdown('core_directives') is None
    dream.mental_model_client.apply_deltas = original
    _session(home, 5)
    repaired = dream.run_dream()
    assert repaired['promoted_count'] == 0
    assert repaired['mental_models_updated'] == 1
    assert LESSON in dream.mental_model_client.get_compiled_markdown('core_directives')
