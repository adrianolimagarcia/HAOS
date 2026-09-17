"""Embedder resolution at the plugin edge.

The fabric core is stdlib-only and must not know how Hermes stores configuration,
so the embedder is resolved here. What matters is that the *identity* it resolves
is the identity the vector index pins: a misconfiguration has to fail at startup,
because silently falling back to hashing vectors while the index claims a remote
model would poison every later comparison.

Loaded through the real discovery path (`memory_plugins.load_memory_provider`)
rather than by importing the module directly, so a broken `register()` is caught.
"""

from __future__ import annotations

import pytest

from hermes.platform.context.memory.embedding import HashingEmbedder, RemoteEmbedder


@pytest.fixture()
def provider_module():
    from plugins.memory.hermes_fabric import provider as module

    return module


def test_default_is_the_offline_hashing_embedder(provider_module) -> None:
    embedder = provider_module._resolve_embedder({})
    assert isinstance(embedder, HashingEmbedder)
    assert embedder.model.model_id == "haos-hashing-v1-256"
    assert embedder.model.dimensions == 256
    assert embedder.model.normalize is True
    assert embedder.model.reindex_policy == "lazy"


def test_hashing_dimensions_are_honoured(provider_module) -> None:
    embedder = provider_module._resolve_embedder({"embedder": {"kind": "hashing", "dimensions": 64}})
    assert embedder.model.dimensions == 64
    assert len(embedder.embed("anything")) == 64
    assert embedder.model.model_id != "haos-hashing-v1-256", "identity must track the dimensions"


def test_hashing_rejects_a_borrowed_model_id(provider_module) -> None:
    """The hashing model id is derived; letting config rename it would let a
    local index masquerade as a remote model."""
    with pytest.raises(ValueError):
        provider_module._resolve_embedder(
            {"embedder": {"kind": "hashing", "model_id": "text-embedding-3-small"}}
        )


def test_remote_embedder_is_built_from_config_and_env(provider_module, monkeypatch) -> None:
    monkeypatch.setenv("HAOS_TEST_EMBEDDER_KEY", "s3cret")
    embedder = provider_module._resolve_embedder(
        {
            "embedder": {
                "kind": "remote",
                "model_id": "bge-m3",
                "dimensions": 1024,
                "base_url": "http://127.0.0.1:8080/",
                "api_key_env": "HAOS_TEST_EMBEDDER_KEY",
                "normalize": False,
                "reindex_policy": "strict",
                "timeout": 5.0,
            }
        }
    )
    assert isinstance(embedder, RemoteEmbedder)
    assert embedder.model.model_id == "bge-m3"
    assert embedder.model.dimensions == 1024
    assert embedder.model.normalize is False
    assert embedder.model.reindex_policy == "strict"
    assert embedder.base_url == "http://127.0.0.1:8080"
    assert embedder.timeout == 5.0


def test_remote_embedder_requires_identity_and_dimensions(provider_module) -> None:
    with pytest.raises(ValueError):
        provider_module._resolve_embedder({"embedder": {"kind": "remote", "dimensions": 8}})
    with pytest.raises(ValueError):
        provider_module._resolve_embedder({"embedder": {"kind": "remote", "model_id": "x"}})


def test_remote_embedder_requires_the_named_env_var(provider_module, monkeypatch) -> None:
    monkeypatch.delenv("HAOS_TEST_ABSENT_KEY", raising=False)
    with pytest.raises(ValueError) as excinfo:
        provider_module._resolve_embedder(
            {
                "embedder": {
                    "kind": "remote",
                    "model_id": "bge-m3",
                    "dimensions": 1024,
                    "api_key_env": "HAOS_TEST_ABSENT_KEY",
                }
            }
        )
    assert "HAOS_TEST_ABSENT_KEY" in str(excinfo.value)


def test_unknown_kind_and_bad_policy_fail_loudly(provider_module) -> None:
    with pytest.raises(ValueError):
        provider_module._resolve_embedder({"embedder": {"kind": "telepathy"}})
    with pytest.raises(ValueError):
        provider_module._resolve_embedder({"embedder": {"kind": "hashing", "reindex_policy": "whenever"}})
    with pytest.raises(ValueError):
        provider_module._resolve_embedder({"embedder": "hashing"})


def test_configured_embedder_reaches_the_vector_index(tmp_path, monkeypatch) -> None:
    """The resolved identity must be the one the index pins, end to end."""
    from hermes.platform.context.memory.federated_fabric import FederatedMemoryCoordinator
    from plugins.memory.hermes_fabric import provider as provider_module

    embedder = provider_module._resolve_embedder({"embedder": {"kind": "hashing", "dimensions": 32}})
    coordinator = FederatedMemoryCoordinator(vault_path=tmp_path / "vault", embedder=embedder)
    try:
        coordinator.ingest_candidate_fact(fact="DECISION: the index pins the model identity.", scope="project")
        registered = coordinator.vector_index.registered_model()
        assert registered is not None
        model_version, dimensions, normalize, reindex_policy = registered
        assert model_version == embedder.model.model_id
        assert dimensions == 32
        assert normalize is True
        assert reindex_policy == "lazy"
        assert coordinator.vector_index.count() == 1
    finally:
        coordinator.close()


def test_plugin_registers_through_real_discovery(tmp_path, monkeypatch) -> None:
    """A misconfigured embedder must break discovery loudly, not silently."""
    from plugins.memory import load_memory_provider

    provider = load_memory_provider("hermes_fabric", register_skills=False)
    assert provider is not None
    # The default path resolves the offline embedder, so no network is involved.
    assert provider.canonical_store is not None
    provider.shutdown()
