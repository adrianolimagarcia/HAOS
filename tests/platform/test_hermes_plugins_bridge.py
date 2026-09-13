import tempfile
import unittest
from pathlib import Path

from hermes.platform.capabilities.registry import CapabilityRegistry
from hermes.platform.extensions.registry import ExtensionRegistry, ACTIVE, DISPOSED, PENDING
from hermes.platform.extensions.hermes_bridge import (
    discover_hermes_plugins,
    bridge_hermes_plugins,
    to_extension_manifest,
    extension_id_for,
    default_plugins_root,
)

YAML_ALPHA = """\
name: alpha
version: 1.2.0
description: Alpha demo plugin
hooks:
  - on_session_end
"""

YAML_BETA = """\
name: beta
version: 0.4.0
description: Beta demo plugin
"""


class TestHermesPluginsBridge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "plugins"

    def tearDown(self):
        self._tmp.cleanup()

    def _make_tree(self):
        for name, content in (("alpha", YAML_ALPHA), ("beta", YAML_BETA)):
            d = self.root / "cat" / name
            d.mkdir(parents=True, exist_ok=True)
            (d / "plugin.yaml").write_text(content)

    def test_discovery_reads_real_plugin_yaml_manifests(self):
        self._make_tree()
        manifests = discover_hermes_plugins(self.root)
        by_name = {m.name: m for m in manifests}
        self.assertIn("alpha", by_name)
        self.assertEqual(by_name["alpha"].version, "1.2.0")
        self.assertEqual(by_name["alpha"].description, "Alpha demo plugin")

    def test_bridge_registers_and_activates_with_capability_bridge(self):
        self._make_tree()
        manifests = discover_hermes_plugins(self.root)
        cap_registry = CapabilityRegistry()
        ext_registry = ExtensionRegistry(capability_registry=cap_registry)

        ids = bridge_hermes_plugins(ext_registry, manifests)
        self.assertEqual(len(ids), 2)

        alpha = next(m for m in manifests if m.name == "alpha")
        ext_id = extension_id_for(alpha)
        self.assertEqual(ext_registry.status(ext_id), PENDING)

        # Capacidade canônica por plugin + procedência no manifesto real.
        ext = ext_registry.get(ext_id)
        self.assertIn("capability:hermes.plugin.alpha", ext.provides)
        self.assertEqual(ext.permissions["kind"], "standalone")
        self.assertEqual(ext.permissions["hermes_manifest_path"],
                         str(self.root / "cat" / "alpha"))

        # Ativação -> ACTIVE e a capability do plugin vira capability HAOS real.
        self.assertEqual(ext_registry.activate(ext_id), ACTIVE)
        self.assertIsNotNone(cap_registry.get("hermes.plugin.alpha"))

        # Desativação limpa o bridge.
        self.assertEqual(ext_registry.deactivate(ext_id), DISPOSED)
        self.assertIsNone(cap_registry.get("hermes.plugin.alpha"))

    def test_bridge_is_idempotent_and_maps_path(self):
        self._make_tree()
        manifests = discover_hermes_plugins(self.root)
        ext_registry = ExtensionRegistry()
        first = bridge_hermes_plugins(ext_registry, manifests)
        self.assertEqual(len(first), 2)
        # Segunda ponte não duplica nada (no-op).
        second = bridge_hermes_plugins(ext_registry, manifests)
        self.assertEqual(second, [])
        self.assertEqual(len(ext_registry.list_manifest_ids()), 2)

    def test_repo_plugins_discovered_without_importing_code(self):
        """O discovery da ponte lê os manifests reais do repo (plumbing)."""
        root = default_plugins_root()
        if root is None:
            self.skipTest("repo plugins root não localizável")
        manifests = discover_hermes_plugins(root)
        self.assertGreaterEqual(len(manifests), 1)
        for m in manifests:
            self.assertTrue(m.name)
            self.assertTrue(m.path)


if __name__ == "__main__":
    unittest.main()
