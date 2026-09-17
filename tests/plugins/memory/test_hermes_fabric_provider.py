from plugins.memory.hermes_fabric.provider import FederatedHermesMemoryProvider
from agent.memory_provider import MemoryProvider


def test_hermes_fabric_plugin_composes_coordinator():
    provider = FederatedHermesMemoryProvider()
    try:
        assert isinstance(provider, MemoryProvider)
        assert provider.coordinator.memory_provider is provider._base
    finally:
        provider.shutdown()
