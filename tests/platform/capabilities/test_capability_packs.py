"""A4 — CapabilityRegistry packs + grafo de dependências (invariantes).

Contratos:
(a) define_pack + resolve: membros de packs entram na resolução; membros
    desconhecidos são ignorados (fail-open como ids avulsos); packs de packs
    achatam sem repetir.
(b) add_dependency: falha rápido para capability desconhecida, autodependência
    e ciclo (CapabilityCycleError); dependências entram ANTES de quem depende
    na resolução (ordem topológica).
(c) Sem packs/deps declarados o resolver preserva a semântica histórica
    (somente os ids pedidos e existentes, na ordem).
"""

import unittest

from hermes.platform.capabilities.registry import (
    Capability, CapabilityRegistry, CapabilityResolver,
    CapabilityCycleError,
)


def _registry_with_extra():
    reg = CapabilityRegistry()
    reg.register(Capability(id="web-search", execution_kind="agentic",
                            providers=["research-worker"]))
    reg.register(Capability(id="vision", execution_kind="agentic",
                            providers=["vision-worker"]))
    return reg


class TestPacks(unittest.TestCase):
    def test_pack_expands_into_members(self):
        reg = _registry_with_extra()
        reg.define_pack("developer", ["code-intelligence", "git", "web-search"])
        caps = CapabilityResolver(reg).resolve_requirements(["developer"])
        self.assertEqual(set(caps), {"code-intelligence", "git", "web-search"})

    def test_pack_ignores_unknown_members(self):
        reg = _registry_with_extra()
        reg.define_pack("dev-plus", ["git", "no-such-cap"])
        caps = CapabilityResolver(reg).resolve_requirements(["dev-plus"])
        self.assertEqual(set(caps), {"git"})

    def test_nested_packs_flatten_without_repeats(self):
        reg = _registry_with_extra()
        reg.define_pack("inner", ["git", "vision"])
        reg.define_pack("outer", ["inner", "git"])
        caps = CapabilityResolver(reg).resolve_requirements(["outer"])
        self.assertEqual(list(caps).count("git"), 1)
        self.assertEqual(set(caps), {"git", "vision"})

    def test_plain_ids_keep_historical_semantics(self):
        reg = _registry_with_extra()
        caps = CapabilityResolver(reg).resolve_requirements(["git", "unknown"])
        self.assertEqual(list(caps), ["git"])


class TestDependencyGraph(unittest.TestCase):
    def test_dependency_precedes_dependent_in_order(self):
        reg = _registry_with_extra()
        reg.add_dependency("vision", "web-search")
        caps = CapabilityResolver(reg).resolve_requirements(["vision"])
        self.assertEqual(list(caps), ["web-search", "vision"])

    def test_transitive_dependencies_ordered(self):
        reg = _registry_with_extra()
        reg.add_dependency("vision", "web-search")
        reg.add_dependency("web-search", "git")
        caps = CapabilityResolver(reg).resolve_requirements(["vision"])
        self.assertEqual(list(caps), ["git", "web-search", "vision"])

    def test_unknown_or_self_dependency_fail_fast(self):
        reg = _registry_with_extra()
        with self.assertRaises(ValueError):
            reg.add_dependency("git", "no-such-cap")
        with self.assertRaises(ValueError):
            reg.add_dependency("git", "git")

    def test_cycle_rejected(self):
        reg = _registry_with_extra()
        reg.add_dependency("vision", "web-search")
        with self.assertRaises(CapabilityCycleError):
            reg.add_dependency("web-search", "vision")
        # Grafo segue íntegro após a tentativa.
        caps = CapabilityResolver(reg).resolve_requirements(["vision"])
        self.assertEqual(list(caps), ["web-search", "vision"])


if __name__ == "__main__":
    unittest.main()
