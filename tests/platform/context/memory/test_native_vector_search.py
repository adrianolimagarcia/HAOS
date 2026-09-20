import sys
import tempfile
import time
from pathlib import Path
import pytest

sys.path.insert(0, ".")
from hermes.platform.context.memory.vector_index import SQLiteVectorIndex


def test_native_vector_index_search_and_fallback(tmp_path):
    db_file = tmp_path / "test_vectors.db"
    idx = SQLiteVectorIndex(db_file, model_version="test-v1", dimensions=4, normalize=True)

    # Inserção de vetores
    idx.upsert("doc1", (1.0, 0.0, 0.0, 0.0))
    idx.upsert("doc2", (0.0, 1.0, 0.0, 0.0))
    idx.upsert("doc3", (0.707, 0.707, 0.0, 0.0))

    # Busca com query alinhada a doc1
    results = idx.search((1.0, 0.0, 0.0, 0.0), limit=2)
    assert len(results) == 2
    assert results[0] == "doc1", "doc1 deve ser o primeiro colocado por proximidade"
    assert results[1] == "doc3", "doc3 deve ser o segundo mais próximo"

    # Busca ortogonal
    results_ortho = idx.search((0.0, 0.0, 1.0, 0.0), limit=2)
    assert len(results_ortho) == 2
