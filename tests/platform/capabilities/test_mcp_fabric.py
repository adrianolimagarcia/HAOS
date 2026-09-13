import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from hermes.platform.capabilities.registry import CapabilityRegistry
from hermes.platform.capabilities.mcp.fabric import (
    MCPFabric, MCPCapabilityProvider, register_mcp_capability,
)


class _TempHome(unittest.TestCase):
    def setUp(self):
        self._old_home = os.environ.get("HERMES_HOME")
        self._tmp = tempfile.TemporaryDirectory(prefix="haos-mcp-")
        os.environ["HERMES_HOME"] = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()
        if self._old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._old_home

    def write_config(self, yaml_text: str) -> pathlib.Path:
        p = pathlib.Path(self._tmp.name) / "config.yaml"
        p.write_text(yaml_text)
        return p


class TestMCPScope(_TempHome):
    def test_scope_pure_by_capabilities(self):
        fabric = MCPFabric(capability_map={"data-access": ["db-mcp"]})
        servers = {"db-mcp": {}, "web-mcp": {}, "fs-mcp": {}}
        self.assertEqual(
            fabric.scope_servers(servers, required_capabilities=["data-access"]),
            ["db-mcp"],
        )

    def test_scope_prefer_plus_required_union(self):
        fabric = MCPFabric(capability_map={
            "data-access": ["db-mcp"], "browse": ["web-mcp"],
        })
        servers = {"db-mcp": {}, "web-mcp": {}, "fs-mcp": {}}
        self.assertEqual(
            fabric.scope_servers(
                servers,
                required_capabilities=["data-access"],
                capabilities_prefer=["browse"],
            ),
            ["db-mcp", "web-mcp"],
        )

    def test_scope_packs_and_allowed_filter(self):
        fabric = MCPFabric()
        fabric.register_pack("data-stack", ["db-mcp", "cache-mcp"])
        servers = {"db-mcp": {}, "cache-mcp": {}, "web-mcp": {}}
        self.assertEqual(
            fabric.scope_servers(servers, mcp_packs=["data-stack"]),
            ["cache-mcp", "db-mcp"],
        )
        # allowed_mcp_names é filtro restritivo final.
        self.assertEqual(
            fabric.scope_servers(
                servers, mcp_packs=["data-stack"], allowed_mcp_names=["db-mcp"]
            ),
            ["db-mcp"],
        )

    def test_scope_empty_when_posture_asks_nothing(self):
        fabric = MCPFabric(capability_map={"data-access": ["db-mcp"]})
        servers = {"db-mcp": {}}
        self.assertEqual(fabric.scope_servers(servers), [])
        self.assertEqual(fabric.scope_servers(servers, mcp_packs=["unknown"]), [])

    def test_scope_resolves_real_posture_and_task_fields(self):
        # scope_servers consome os campos REAIS dos specs: PostureSpec.
        # capabilities_prefer e TaskSpec.mcp_packs/required_capabilities.
        from hermes.platform.posture.specs import PostureSpec
        from hermes.platform.tasks.spec import TaskSpec

        fabric = MCPFabric(capability_map={
            "code-intelligence": ["lsp-mcp"], "data-access": ["db-mcp"],
        })
        fabric.register_pack("data-stack", ["db-mcp", "cache-mcp"])
        posture = PostureSpec(
            id="implementer", name="Implementer", description="d",
            capabilities_prefer=["code-intelligence"],
        )
        task = TaskSpec(id="T-1", title="t", goal="g", mcp_packs=["data-stack"])
        servers = {"lsp-mcp": {}, "db-mcp": {}, "cache-mcp": {}, "web-mcp": {}}
        names = fabric.scope_servers(
            servers,
            required_capabilities=task.required_capabilities,
            capabilities_prefer=posture.capabilities_prefer,
            mcp_packs=task.mcp_packs,
        )
        self.assertEqual(names, ["cache-mcp", "db-mcp", "lsp-mcp"])


class TestMCPDiscoveryHealth(_TempHome):
    def test_stdlib_degradation_no_config(self):
        # Sem config.yaml: discovery vazio e health vazio — degradação limpa,
        # nada é spawnado e nada quebra sem o SDK ``mcp``.
        fabric = MCPFabric()
        self.assertEqual(fabric.discover_servers(), {})
        self.assertEqual(fabric.health(), [])
        self.assertEqual(fabric.discover_tools(), [])
        self.assertEqual(fabric.probe_once(), {})

    def test_stdlib_only_module_import_and_degradation(self):
        # O fabric importa stdlib-only no nível de módulo e degrada para []/{}
        # quando o extra ``mcp`` do kernel NÃO está instalado: roda um
        # interpretador -S (sem site-packages) com o checkout no sys.path e
        # prova que módulo importa e as superfícies degradam ({} / [] / []).
        root = pathlib.Path(__file__).resolve().parents[3]
        script = (
            "import json;"
            "import hermes.platform.capabilities.mcp.fabric as f;"
            "fab = f.MCPFabric();"
            "out = {"
            "'servers': fab.discover_servers(), 'health': fab.health(),"
            "'tools': fab.discover_tools(), 'probe': fab.probe_once(),"
            "'scope': fab.scope_servers({}, capabilities_prefer=['x']),"
            "'provider': f.MCPCapabilityProvider(fabric=fab).provider_id"
            "};"
            "print(json.dumps(out))"
        )
        proc = subprocess.run(
            [sys.executable, "-S", "-c", script],
            cwd=str(root), capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(out["servers"], {})
        self.assertEqual(out["health"], [])
        self.assertEqual(out["tools"], [])
        self.assertEqual(out["probe"], {})
        self.assertEqual(out["scope"], [])
        self.assertEqual(out["provider"], "hermes-mcp-fabric")

    def test_discovery_reads_real_config_and_health(self):
        self.write_config(
            "mcp_servers:\n"
            "  demo:\n"
            "    command: python3\n"
            "    args: [\"-m\", \"demo_server\"]\n"
            "    enabled: true\n"
        )
        fabric = MCPFabric(capability_map={"demo-cap": ["demo"]})
        servers = fabric.discover_servers()
        self.assertIn("demo", servers)
        self.assertEqual(servers["demo"]["command"], "python3")

        # Scope decide sobre o que o kernel configurou de verdade.
        self.assertEqual(
            fabric.scope_servers(servers, required_capabilities=["demo-cap"]),
            ["demo"],
        )
        self.assertEqual(fabric.scope_servers(servers), [])

        statuses = fabric.health()
        self.assertEqual(len(statuses), 1)
        entry = statuses[0]
        self.assertEqual(entry["name"], "demo")
        self.assertIn(entry["status"], {"connected", "disabled", "connecting", "failed", "configured"})


class TestMCPCapabilityProvider(_TempHome):
    def test_register_and_probe(self):
        registry = CapabilityRegistry()
        provider = register_mcp_capability(registry)

        cap = registry.get("mcp")
        self.assertIsNotNone(cap)
        self.assertEqual(cap.execution_kind, "agentic")
        self.assertIn(provider.provider_id, cap.providers)
        self.assertIs(registry.get_provider("hermes-mcp-fabric"), provider)

        probe = asyncio.run(provider.probe())
        self.assertTrue(probe["available"])
        self.assertEqual(probe["discovered"], 0)  # sem config: degraded, não erro

    def test_acquire_scopes_from_posture_and_task(self):
        # Server REAL configurado (config.yaml) entra no escopo da capability.
        self.write_config(
            "mcp_servers:\n"
            "  db-mcp:\n"
            "    command: python3\n"
            "    args: [\"-m\", \"db_server\"]\n"
            "    enabled: true\n"
        )
        fabric = MCPFabric(capability_map={"data-access": ["db-mcp"]})
        provider = MCPCapabilityProvider(fabric=fabric)
        handle = asyncio.run(provider.acquire({
            "required_capabilities": ["data-access"],
        }))
        self.assertEqual(handle["server_names"], ["db-mcp"])
        # release é no-op (o fabric não é dono dos servers do kernel).
        self.assertIsNone(asyncio.run(provider.release(handle)))

    def test_acquire_no_scope_when_posture_silent(self):
        provider = MCPCapabilityProvider(fabric=MCPFabric(capability_map={"x": ["db"]}))
        handle = asyncio.run(provider.acquire({}))
        self.assertEqual(handle["server_names"], [])

    def test_provider_unregister_removes_capability(self):
        # register/unregister no CapabilityRegistry: remove_provider tira o
        # provider; sem providers restantes a capability ``mcp`` sai do registry.
        registry = CapabilityRegistry()
        provider = register_mcp_capability(registry)
        self.assertIsNotNone(registry.get("mcp"))
        self.assertIs(registry.get_provider(provider.provider_id), provider)

        removed = registry.remove_provider("mcp", provider.provider_id)
        self.assertTrue(removed)
        self.assertIsNone(registry.get("mcp"))


if __name__ == "__main__":
    unittest.main()
