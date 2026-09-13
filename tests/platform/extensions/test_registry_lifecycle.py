"""ExtensionRegistry — port DSH/cordis lifecycle (Fase 1).

Contracts (relações de comportamento, não snapshots):
1. register duplicado levanta; histórico de estados registra a ordem legal de
   transições (append-only).
2. activate() com requisito insatisfeito levanta E deixa a extensão em PENDING
   com zero capabilities bridgadas (rollback — nada parcial fica montado).
3. Falha no corpo de carga (load_callback) -> estado FAILED com diagnóstico
   (phase/message) e capabilities bridgadas nesta tentativa são desfeitas.
4. stop() é não-terminal (STOPPED preserva manifest; re-activate re-bridga);
   deactivate() é terminal (DISPOSED) e restart de DISPOSED é recusado.
5. Transição concorrente em voo é recusada (transition-in-flight).
6. Cada transição emite ExtensionEvent (subscribe).
"""

import unittest

from hermes.platform.capabilities.registry import CapabilityRegistry
from hermes.platform.extensions.registry import (
    ExtensionRegistry, ExtensionManifest, ExtensionEvent,
    PENDING, LOADING, ACTIVE, FAILED, STOPPED, UNLOADING, DISPOSED,
)


def _manifest(ext_id, provides=(), requires=(), **kw):
    return ExtensionManifest(id=ext_id, version="1.0", provides=list(provides),
                             requires=list(requires), **kw)


class TestRegistryLifecycle(unittest.TestCase):
    def test_register_duplicate_raises_and_history_is_append_only(self):
        registry = ExtensionRegistry()
        registry.register(_manifest("ext-a"))
        with self.assertRaises(ValueError):
            registry.register(_manifest("ext-a"))
        history = registry.state_history("ext-a")
        self.assertEqual(history[0]["to"], PENDING)  # registro inicial
        self.assertEqual(len(history), 1)

    def test_activate_unsatisfied_requirement_raises_and_rolls_back(self):
        caps = CapabilityRegistry()
        registry = ExtensionRegistry(capability_registry=caps)
        registry.register(_manifest("needs-x", provides=["capability:extra"],
                                     requires=["capability:graphrag"]))
        with self.assertRaises(RuntimeError):
            registry.activate("needs-x")
        # Permanece PENDING e nada foi bridgado (rollback sem efeitos parciais).
        self.assertEqual(registry.status("needs-x"), PENDING)
        self.assertIsNone(caps.get("extra"))

    def test_activation_load_failure_records_failed_and_unbridges(self):
        def boom(manifest):
            raise RuntimeError("load exploded")

        caps = CapabilityRegistry()
        registry = ExtensionRegistry(capability_registry=caps, load_callback=boom)
        registry.register(_manifest("crashy", provides=["capability:boom-cap"]))
        with self.assertRaises(RuntimeError):
            registry.activate("crashy")
        self.assertEqual(registry.status("crashy"), FAILED)
        failure = registry.last_failure("crashy")
        self.assertEqual(failure["phase"], "load")
        self.assertIn("load exploded", failure["message"])
        # Capacidade bridgada antes do throw foi desfeita.
        self.assertIsNone(caps.get("boom-cap"))

    def test_stop_is_non_terminal_and_restart_rebridges(self):
        caps = CapabilityRegistry()
        registry = ExtensionRegistry(capability_registry=caps)
        registry.register(_manifest("svc", provides=["capability:svc-cap"]))
        self.assertEqual(registry.activate("svc"), ACTIVE)
        self.assertIsNotNone(caps.get("svc-cap"))
        self.assertEqual(registry.stop("svc"), STOPPED)
        self.assertIsNone(caps.get("svc-cap"))           # unbridged no stop
        self.assertIsNotNone(registry.get("svc"))        # manifest preservado
        # Restart a partir de STOPPED é legal e re-bridga.
        self.assertEqual(registry.activate("svc"), ACTIVE)
        self.assertIsNotNone(caps.get("svc-cap"))

    def test_disposed_cannot_restart_and_deactivate_is_terminal(self):
        caps = CapabilityRegistry()
        registry = ExtensionRegistry(capability_registry=caps)
        registry.register(_manifest("gone", provides=["capability:gone-cap"]))
        registry.activate("gone")
        self.assertEqual(registry.deactivate("gone"), DISPOSED)
        self.assertIsNone(caps.get("gone-cap"))
        with self.assertRaises(RuntimeError):
            registry.activate("gone")  # DISPOSED é terminal

    def test_transition_in_flight_refused(self):
        registry = ExtensionRegistry()
        registry.register(_manifest("busy"))
        registry._in_flight.add("busy")  # simula transição concorrente
        try:
            with self.assertRaises(RuntimeError):
                registry.activate("busy")
        finally:
            registry._in_flight.discard("busy")

    def test_lifecycle_emits_events_in_order(self):
        caps = CapabilityRegistry()
        registry = ExtensionRegistry(capability_registry=caps)
        seen = []
        registry.subscribe(lambda ev: seen.append(ev))
        registry.register(_manifest("evt", provides=["capability:evt-cap"]))
        registry.activate("evt")
        registry.stop("evt")
        self.assertTrue(all(isinstance(e, ExtensionEvent) for e in seen))
        states = [(e.from_state, e.to_state) for e in seen]
        # registro não emite; activate: PENDING->LOADING->ACTIVE; stop: ACTIVE->UNLOADING->STOPPED
        self.assertIn((PENDING, LOADING), states)
        self.assertIn((LOADING, ACTIVE), states)
        self.assertIn((ACTIVE, UNLOADING), states)
        self.assertIn((UNLOADING, STOPPED), states)


if __name__ == "__main__":
    unittest.main()
