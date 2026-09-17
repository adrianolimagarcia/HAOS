"""Embedding models for the canonical Memory Fabric vector projection.

Three things must be pinned for a vector index to be reproducible, and all three
travel together in :class:`EmbeddingModel`:

* ``model_id`` — identity AND version. A vector is only comparable to another
  vector produced by the same ``model_id``, so the id is part of the index key
  and changing it is what triggers a reindex.
* ``dimensions`` — the width of every vector; a mismatch is rejected rather than
  silently truncated.
* ``normalize`` — whether vectors are L2-normalized on write. Cosine similarity
  is used at search time either way, so the flag is recorded for auditability and
  for providers whose vectors are already unit-length.

Stdlib-only: the default embedder is a deterministic hashing model that needs no
network, and the remote embedder speaks the OpenAI-compatible ``/embeddings``
contract with ``urllib``. A caller that wants a different backend implements
:class:`Embedder` and injects it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol, Sequence, Tuple

logger = logging.getLogger(__name__)

# Reindex policy values. ``strict`` refuses to serve vectors from another model;
# ``lazy`` serves nothing for the stale model but leaves the rows in place so a
# rollback to the previous model_id is instant.
REINDEX_STRICT = "strict"
REINDEX_LAZY = "lazy"

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True)
class EmbeddingModel:
    """Identity, shape and normalization policy of one embedding model."""

    model_id: str
    dimensions: int
    normalize: bool = True
    reindex_policy: str = REINDEX_LAZY

    def __post_init__(self) -> None:
        if not self.model_id or not self.model_id.strip():
            raise ValueError("model_id must not be empty")
        if self.dimensions <= 0:
            raise ValueError("dimensions must be positive")
        if self.reindex_policy not in (REINDEX_STRICT, REINDEX_LAZY):
            raise ValueError("unknown reindex policy: %r" % self.reindex_policy)


class Embedder(Protocol):
    """Minimal contract the vector projection depends on."""

    @property
    def model(self) -> EmbeddingModel: ...

    def embed(self, text: str) -> Tuple[float, ...]: ...


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _l2(vector: Sequence[float]) -> Tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return tuple(float(value) for value in vector)
    return tuple(float(value) / norm for value in vector)


class HashingEmbedder:
    """Deterministic, offline, dependency-free embedder.

    Signed feature hashing over word unigrams and bigrams, projected into a
    fixed number of buckets. It is not a semantic model, but it is *stable*:
    the same text always yields the same vector, on any host, forever — which is
    what makes the vector projection rebuildable and testable offline. Deployments
    that need semantics inject :class:`RemoteEmbedder` instead.
    """

    VERSION = "haos-hashing-v1"

    def __init__(self, dimensions: int = 256) -> None:
        self._model = EmbeddingModel(
            model_id="%s-%d" % (self.VERSION, dimensions), dimensions=dimensions
        )

    @property
    def model(self) -> EmbeddingModel:
        return self._model

    def _features(self, text: str) -> List[str]:
        tokens = _tokenize(text)
        features = list(tokens)
        features.extend("%s_%s" % (a, b) for a, b in zip(tokens, tokens[1:]))
        return features

    def embed(self, text: str) -> Tuple[float, ...]:
        buckets = [0.0] * self._model.dimensions
        for feature in self._features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            index = value % self._model.dimensions
            buckets[index] += 1.0 if value & 1 else -1.0
        return _l2(buckets) if self._model.normalize else tuple(buckets)


class RemoteEmbedder:
    """OpenAI-compatible ``/embeddings`` client, stdlib-only.

    The model id and dimensions are configured explicitly rather than discovered:
    a provider that silently swaps the served model would otherwise corrupt every
    stored vector, and a wrong dimension must fail loudly at the first call.
    """

    def __init__(
        self,
        *,
        model_id: str,
        dimensions: int,
        base_url: str,
        api_key: Optional[str] = None,
        normalize: bool = True,
        timeout: float = 20.0,
        reindex_policy: str = REINDEX_LAZY,
    ) -> None:
        self._model = EmbeddingModel(
            model_id=model_id,
            dimensions=dimensions,
            normalize=normalize,
            reindex_policy=reindex_policy,
        )
        self.base_url = base_url.rstrip("/")
        self._credential = api_key
        self.timeout = timeout

    @property
    def model(self) -> EmbeddingModel:
        return self._model

    def embed(self, text: str) -> Tuple[float, ...]:
        payload = json.dumps({"model": self._model.model_id, "input": text}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self._credential:
            request.add_header("Authorization", "Bearer " + self._credential)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        vector = tuple(float(value) for value in body["data"][0]["embedding"])
        if len(vector) != self._model.dimensions:
            raise ValueError(
                "embedder %s returned %d dimensions, configured for %d"
                % (self._model.model_id, len(vector), self._model.dimensions)
            )
        return _l2(vector) if self._model.normalize else vector


def validate_vector(vector: Sequence[float], model: EmbeddingModel) -> Tuple[float, ...]:
    """Shape/NaN gate applied before any vector reaches the index."""
    if not vector:
        raise ValueError("embedding must not be empty")
    if len(vector) != model.dimensions:
        raise ValueError(
            "embedding has %d dimensions, model %s expects %d"
            % (len(vector), model.model_id, model.dimensions)
        )
    values = tuple(float(value) for value in vector)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("embedding contains a non-finite value")
    return values
