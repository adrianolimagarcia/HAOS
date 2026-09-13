"""ExtensionRegistry — dependências (port DSH/cordis, Fase 1).

Contracts (relações de comportamento, não snapshots):
1. hold-mode estaciona a extensão em PENDING com waiting_for=[cap ausente]
   (sem levantar, sem bridgar).
2. A primeira ativação de uma extensão que bridga a capacidade ausente promove
   automaticamente a estacionada (auto-activation); retry_waiting() faz o
   mesmo sob demanda.
3. deactivate(cascade=True) drena dependentes ACTIVE ANTES do provider
   (dependent-first); deactivate(cascade=False) recusa enquanto houver
   dependentes ACTIVE.
4. Providers duplicados de uma mesma capability fazem UNION (ambos registrados
   no CapabilityRegistry); unbridge remove apenas o provider da extensão.
5. Requisitos opcionais nunca gateiam a ativação.
"""

import unittest

from hermes.platform.capabilities.registry import CapabilityRegistry
from hermes.platform.extensions.registry import (
    ExtensionRegistry, ExtensionManifest,
    PENDING, ACTIVE, DISPOSED,
)


def _manifest(ext_id, provides=(), requires=()):
    return ExtensionManifest(id=ext_id, version="1.0",
                             provides=list(provides), requires=list(requires))


class TestRegistryDependencies(unittest.TestCase):
    def test_hold_mode_parks_and_auto_promotes(self):
        caps = CapabilityRegistry()
        registry = ExtensionRegistry(capability_registry=caps)
        registry.register(_manifest("consumer", requires=["capability:db-svc"]))
        # hold: estaciona em PENDING com waiting_for preenchido — sem levantar.
        self.assertEqual(
            registry.activate_with_deps("consumer", on_missing="hold"), PENDING
        )
        self.assertEqual(registry.status("consumer"), PENDING)
        self.assertEqual(registry.waiting_for("consumer"), ["db-svc"])
        # Provider ativa -> promove automaticamente a estacionada.
        registry.register(_manifest("provider", provides=["capability:db-svc"]))
        self.assertEqual(registry.activate("provider"), ACTIVE)
        self.assertEqual(registry.status("consumer"), ACTIVE)
        self.assertEqual(registry.waiting_for("consumer"), [])

    def test_retry_waiting_promotes_parked(self):
        caps = CapabilityRegistry()
        registry = ExtensionRegistry(capability_registry=caps)
        registry.register(_manifest("consumer", requires=["capability:cache"]))
        registry.activate_with_deps("consumer", on_missing="hold")
        # Capacidade chega direto no CapabilityRegistry (sem extensão ACTIVE).
        caps.add_provider("cache", "native")
        promoted = registry.retry_waiting()
        self.assertIn("consumer", promoted)
        self.assertEqual(registry.status("consumer"), ACTIVE)

    def test_deactivate_cascade_is_dependent_first(self):
        caps = CapabilityRegistry()
        registry = ExtensionRegistry(capability_registry=caps)
        registry.register(_manifest("core", provides=["capability:core-api"]))
        registry.register(_manifest("leaf", requires=["capability:core-api"]))
        registry.activate("core")
        registry.activate("leaf")
        # Sem cascade: provider com dependente ACTIVE recusa.
        with self.assertRaises(RuntimeError):
            registry.deactivate("core")
        self.assertEqual(registry.status("core"), ACTIVE)
        # Com cascade: leaf é desativado antes de core.
        self.assertEqual(registry.deactivate("core", cascade=True), DISPOSED)
        self.assertEqual(registry.status("leaf"), DISPOSED)
        self.assertIsNone(caps.get("core-api"))

    def test_duplicate_providers_union_and_partial_unbridge(self):
        caps = CapabilityRegistry()
        registry = ExtensionRegistry(capability_registry=caps)
        registry.register(_manifest("p1", provides=["capability:shared-cap"]))
        registry.register(_manifest("p2", provides=["capability:shared-cap"]))
        registry.activate("p1")
        registry.activate("p2")
        capability = caps.get("shared-cap")
        self.assertIn("p1", capability.providers)
        self.assertIn("p2", capability.providers)  # UNION, não last-writer-wins
        # Unbridge do p2 não derruba a capability do p1.
        registry.deactivate("p2")
        capability = caps.get("shared-cap")
        self.assertIsNotNone(capability)
        self.assertIn("p1", capability.providers)
        self.assertNotIn("p2", capability.providers)
        # Unbridge do último provider remove a capability.
        registry.deactivate("p1")
        self.assertIsNone(caps.get("shared-cap"))

    def test_optional_requirements_never_gate(self):
        registry = ExtensionRegistry(capability_registry=CapabilityRegistry())
        manifest = ExtensionManifest(
            id="soft", version="1.0", requires=[], optional=["capability:graphrag"],
        )
        registry.register(manifest)
        self.assertEqual(registry.activate("soft"), ACTIVE)


if __name__ == "__main__":
    unittest.main()
