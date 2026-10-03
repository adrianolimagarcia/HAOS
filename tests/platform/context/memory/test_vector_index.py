from hermes.platform.context.memory.vector_index import SQLiteVectorIndex

def test_versioned_vector_index_returns_canonical_ids(tmp_path):
    index = SQLiteVectorIndex(tmp_path / "vectors.db", "test-v1")
    index.upsert("a", [1.0, 0.0])
    index.upsert("b", [0.0, 1.0])
    assert index.search([0.9, 0.1]) == ["a", "b"]
    index.close()
