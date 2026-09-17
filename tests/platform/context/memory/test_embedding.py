"""The embedder contract, including a real HTTP round trip.

`RemoteEmbedder` speaks to a live endpoint, so it is tested against a real
loopback server rather than a mocked `urlopen`: the things that actually break —
request shape, auth header, response parsing, dimension enforcement, L2
normalization — only exist once bytes move. A stub would have hidden the
`NameError` in the constructor for exactly as long as it did.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Tuple

import pytest

from hermes.platform.context.memory.embedding import (
    EmbeddingModel,
    HashingEmbedder,
    RemoteEmbedder,
    validate_vector,
)


class _EmbeddingHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible ``/embeddings`` endpoint."""

    vectors: Dict[str, Tuple[float, ...]] = {}
    seen: List[Dict[str, Any]] = []
    status = 200
    body: Any = None

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).seen.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), "payload": payload}
        )
        if type(self).body is not None:
            response = type(self).body
        else:
            response = {"data": [{"embedding": list(type(self).vectors.get(payload["input"], (0.0, 0.0, 0.0, 0.0)))}]}
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args: Any) -> None:  # keep the test output clean
        return


@pytest.fixture()
def endpoint():
    _EmbeddingHandler.vectors = {}
    _EmbeddingHandler.seen = []
    _EmbeddingHandler.status = 200
    _EmbeddingHandler.body = None
    server = HTTPServer(("127.0.0.1", 0), _EmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_remote_embedder_posts_and_normalizes(endpoint) -> None:
    _EmbeddingHandler.vectors = {"retention window": (3.0, 4.0, 0.0, 0.0)}
    embedder = RemoteEmbedder(
        model_id="bge-m3", dimensions=4, base_url=endpoint, api_key="s3cret",
    )
    vector = embedder.embed("retention window")

    assert vector == pytest.approx((0.6, 0.8, 0.0, 0.0)), "the vector was not L2-normalized"
    request = _EmbeddingHandler.seen[-1]
    assert request["path"] == "/embeddings"
    assert request["auth"] == "Bearer s3cret"
    assert request["payload"] == {"model": "bge-m3", "input": "retention window"}


def test_remote_embedder_omits_auth_when_no_key_is_configured(endpoint) -> None:
    embedder = RemoteEmbedder(model_id="local", dimensions=4, base_url=endpoint + "/")
    embedder.embed("anything")
    assert _EmbeddingHandler.seen[-1]["auth"] is None


def test_remote_embedder_rejects_a_wrong_dimension(endpoint) -> None:
    """A provider that swaps the served model must fail, not store unusable vectors."""
    _EmbeddingHandler.vectors = {"anything": (1.0, 2.0, 3.0)}
    embedder = RemoteEmbedder(model_id="bge-m3", dimensions=1024, base_url=endpoint)
    with pytest.raises(ValueError) as excinfo:
        embedder.embed("anything")
    assert "3 dimensions" in str(excinfo.value)
    assert "1024" in str(excinfo.value)


def test_remote_embedder_identity_is_pinned_to_configuration(endpoint) -> None:
    embedder = RemoteEmbedder(
        model_id="bge-m3",
        dimensions=1024,
        base_url=endpoint,
        normalize=False,
        reindex_policy="strict",
    )
    assert embedder.model.model_id == "bge-m3"
    assert embedder.model.dimensions == 1024
    assert embedder.model.normalize is False
    assert embedder.model.reindex_policy == "strict"


def test_hashing_embedder_is_deterministic_normalized_and_discriminating() -> None:
    embedder = HashingEmbedder(64)
    first = embedder.embed("the canonical journal is the source of truth")
    again = embedder.embed("the canonical journal is the source of truth")
    different = embedder.embed("deployments use kubernetes")

    assert first == again, "hashing must be deterministic"
    assert len(first) == 64
    assert sum(value * value for value in first) ** 0.5 == pytest.approx(1.0, abs=1e-9)
    assert first != different
    # Unrelated text must not collide into a near-identical vector, or the vector
    # channel would add noise instead of candidates.
    similarity = sum(a * b for a, b in zip(first, different))
    assert similarity < 0.9


def test_embedding_model_rejects_incoherent_identity() -> None:
    with pytest.raises(ValueError):
        EmbeddingModel("", 8)
    with pytest.raises(ValueError):
        EmbeddingModel("model", 0)
    with pytest.raises(ValueError):
        EmbeddingModel("model", 8, reindex_policy="whenever")


def test_validate_vector_gates_shape_and_nan() -> None:
    model = EmbeddingModel("model", 3)
    assert validate_vector([1.0, 2.0, 3.0], model) == (1.0, 2.0, 3.0)
    with pytest.raises(ValueError):
        validate_vector([], model)
    with pytest.raises(ValueError):
        validate_vector([1.0, 2.0], model)
    with pytest.raises(ValueError):
        validate_vector([1.0, float("nan"), 3.0], model)
