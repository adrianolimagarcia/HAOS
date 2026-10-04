from hermes.platform.context.memory.vector_index import SQLiteVectorIndex

def test_versioned_vector_index_returns_canonical_ids(tmp_path):
    index = SQLiteVectorIndex(tmp_path / "vectors.db", "test-v1")
    index.upsert("a", [1.0, 0.0])
    index.upsert("b", [0.0, 1.0])
    assert index.search([0.9, 0.1]) == ["a", "b"]
    index.close()


def test_upsert_batch_matches_row_by_row_contract(tmp_path, monkeypatch):
    """Batch (native or fallback) must land the same rows as per-row upsert.

    Contract between the two write paths: identical search results for the
    same vectors, and the batch fsync win must not change what is readable.
    """
    vectors = {"a": [1.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0], "c": [0.9, 0.1, 0.0]}

    rowwise = SQLiteVectorIndex(tmp_path / "row.db", "batch-parity")
    for rid, vec in vectors.items():
        rowwise.upsert(rid, vec)

    batched = SQLiteVectorIndex(tmp_path / "batch.db", "batch-parity")
    assert batched.upsert_batch(list(vectors.items())) == len(vectors)

    query = [0.95, 0.05, 0.0]
    assert batched.search(query, limit=3) == rowwise.search(query, limit=3)

    # REPLACE semantics: re-batching the same ids must not duplicate rows.
    assert batched.upsert_batch(list(vectors.items())) == len(vectors)
    rows = batched.db.execute(
        "SELECT COUNT(*) FROM memory_vectors WHERE model_version='batch-parity'"
    ).fetchone()[0]
    assert rows == len(vectors)


def test_upsert_batch_validation_and_empty_are_safe(tmp_path):
    index = SQLiteVectorIndex(tmp_path / "val.db", "batch-val")
    assert index.upsert_batch([]) == 0

    for bad in (
        [("", [1.0])],                # empty record id
        [("x", [])],                  # empty vector
        [("x", [1.0, float("nan")])], # non-finite
    ):
        try:
            index.upsert_batch(bad)
            raise AssertionError("expected ValueError for %r" % (bad,))
        except ValueError:
            pass

    # Nothing from the rejected batches may reach the database.
    assert index.search([1.0, 0.0], limit=5) == []

    # Mixed dimensions inside one batch is refused (model pin is global).
    index.upsert_batch([("x", [1.0, 0.0])])
    try:
        index.upsert_batch([("y", [1.0, 0.0, 0.0])])
        raise AssertionError("dimension drift must be refused")
    except ValueError:
        pass


def test_upsert_batch_fallback_path_parity(tmp_path, monkeypatch):
    """With the native kernel hidden, the Python fallback must satisfy the
    same read contract — the graceful-degradation branch is tested, not hoped."""
    monkeypatch.setattr(SQLiteVectorIndex, "_native_lib", None, raising=False)
    index = SQLiteVectorIndex(tmp_path / "fb.db", "batch-fb")
    written = index.upsert_batch([
        ("p", [0.0, 1.0]),
        ("q", [1.0, 0.0]),
    ])
    assert written == 2
    assert index.search([0.8, 0.2], limit=2) == ["q", "p"]
