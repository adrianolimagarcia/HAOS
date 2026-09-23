"""Tests for the live harness registry and worker allocation."""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from hermes.platform.observability.event_store import EventStore
from hermes.platform.workers.harness_registry import (
    HarnessRegistry,
    HarnessInfo,
    HarnessUnavailableError,
)
from hermes.platform.workers.external import resolve_external_worker
from hermes.platform.workers import harness_discovery
def _reset_discovery():
    """Reset the discovery singleton so each test scans fresh dirs."""
    harness_discovery._singleton = None


class TestHarnessRegistry(unittest.TestCase):
    def setUp(self):
        self.registry = HarnessRegistry()

    def test_native_always_available(self):
        info = self.registry.detect("native")
        self.assertTrue(info.available)
        self.assertEqual(info.status, "available")
        self.assertEqual(info.name, "native")

    def test_dsh_detection_found_and_missing(self):
        with patch("shutil.which", return_value="/usr/local/bin/dsh"):
            info = self.registry.detect("dsh")
            self.assertTrue(info.available)
            self.assertEqual(info.status, "available")
            self.assertEqual(info.executable, "/usr/local/bin/dsh")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            empty = tmp_path / "empty"
            empty.mkdir()
            _reset_discovery()
            with patch("shutil.which", return_value=None), \
                 patch.dict(os.environ, {"HAOS_HARNESS_SEARCH_DIRS": str(empty)}, clear=True), \
                 patch.object(Path, "home", classmethod(lambda cls: tmp_path)), \
                 patch.object(Path, "cwd", classmethod(lambda cls: tmp_path)), \
                 patch.object(harness_discovery, "_HAOS_BIN", tmp_path / ".haos" / "bin"):
                info = self.registry.detect("dsh")
                self.assertFalse(info.available)
                self.assertEqual(info.status, "missing")
                self.assertTrue(bool(info.error and "not found" in info.error))
                self.assertEqual(info.details.get("fallback"), "native")
                self.assertTrue(info.details.get("searched"))

    def test_opencode_detection(self):
        with patch("shutil.which", return_value="/opt/bin/opencode"):
            info = self.registry.detect("opencode")
            self.assertTrue(info.available)
            self.assertEqual(info.executable, "/opt/bin/opencode")

    def test_agy_detection(self):
        with patch("shutil.which", return_value="/opt/bin/agy"):
            info = self.registry.detect("agy")
            self.assertTrue(info.available)
            self.assertEqual(info.name, "agy")

    def test_allocate_agy_uses_real_cli_print_mode(self):
        with patch("shutil.which", return_value="/usr/local/bin/agy"):
            spec = self.registry.allocate("agy", {
                "task_id": "T-AGY",
                "title": "Inspect adapter",
                "workspace": "/tmp/test-ws",
                "model": "gemini-3.8-flash-low",
                "effort": "low",
            })
        self.assertEqual(spec.kind, "agy")
        self.assertEqual(spec.args[:4], ["/usr/local/bin/agy", "--print", "Inspect adapter", "--add-dir"])
        self.assertIn("/tmp/test-ws", spec.args)
        self.assertIn("--model", spec.args)
        self.assertNotIn("chat", spec.args)
        self.assertNotIn("ANTIGRAVITY_PROXY_ENDPOINT", spec.env_vars)

        self.registry.register_detector(
            "custom-runner",
            lambda: HarnessInfo(
                name="custom-runner",
                available=True,
                status="available",
                executable="/usr/bin/custom",
                description="Custom tool",
            )
        )
        info = self.registry.detect("custom-runner")
        self.assertTrue(info.available)
        self.assertEqual(info.executable, "/usr/bin/custom")

    def test_allocate_dsh_success(self):
        with patch("shutil.which", return_value="/bin/dsh"):
            spec = self.registry.allocate("dsh", {
                "task_id": "T-100",
                "title": "Refactor parser",
                "workspace": "/tmp/test-ws",
            })
            self.assertEqual(spec.kind, "dsh")
            self.assertEqual(spec.executable, "/bin/dsh")
            self.assertEqual(spec.args, ["/bin/dsh", "--profile", "headless", "Refactor parser"])
            self.assertNotIn("exec", spec.args)
            self.assertNotIn("--objective", spec.args)
            self.assertNotIn("--workdir", spec.args)
            self.assertEqual(spec.workspace, "/tmp/test-ws")
            self.assertEqual(spec.env_vars.get("HAOS_KANBAN_TASK_ID"), "T-100")

    def test_allocate_missing_harness_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            empty = tmp_path / "empty"
            empty.mkdir()
            _reset_discovery()
            with patch("shutil.which", return_value=None), \
                 patch.dict(os.environ, {"HAOS_HARNESS_SEARCH_DIRS": str(empty)}, clear=True), \
                 patch.object(Path, "home", classmethod(lambda cls: tmp_path)), \
                 patch.object(Path, "cwd", classmethod(lambda cls: tmp_path)), \
                 patch.object(harness_discovery, "_HAOS_BIN", tmp_path / ".haos" / "bin"):
                with self.assertRaises(HarnessUnavailableError):
                    self.registry.allocate("dsh", {"task_id": "T-100"})

    def test_external_worker_resolver_integration(self):
        with patch("shutil.which", return_value="/bin/opencode"):
            spec = resolve_external_worker("opencode", {
                "task_id": "T-200",
                "title": "AST analysis",
                "workspace": "/tmp",
            })
            self.assertIsNotNone(spec)
            assert spec is not None
            self.assertEqual(spec.kind, "opencode")
            self.assertIn("AST analysis", spec.args)


class TestHarnessAutoDiscovery(unittest.TestCase):
    """Filesystem search, PATH installation and native fallback behavior."""

    def setUp(self):
        _reset_discovery()

    def test_binary_found_on_filesystem_is_installed_to_haos_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tool_root = tmp_path / "search"
            bin_dir = tool_root / "bin"
            bin_dir.mkdir(parents=True)
            opencode = bin_dir / "opencode"
            opencode.write_text("#!/bin/sh\necho opencode\n")
            opencode.chmod(opencode.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            haos_bin = tmp_path / ".haos" / "bin"
            with patch("shutil.which", return_value=None), \
                 patch.dict(os.environ, {"HAOS_HARNESS_SEARCH_DIRS": str(tool_root)}, clear=True), \
                 patch.object(Path, "home", classmethod(lambda cls: tmp_path)), \
                 patch.object(Path, "cwd", classmethod(lambda cls: tmp_path)), \
                 patch.object(harness_discovery, "_HAOS_BIN", haos_bin):
                registry = HarnessRegistry()
                info = registry.detect("opencode")
                self.assertTrue(info.available, f"expected auto-discovery to find opencode: {info}")
                self.assertEqual(info.status, "available")
                self.assertTrue(info.details.get("auto_discovered"))
                self.assertIsNotNone(info.executable)
                assert info.executable is not None
                launcher = Path(info.executable)
                self.assertTrue(launcher.is_file() or launcher.is_symlink())
                self.assertEqual(launcher.parent, haos_bin)
                # The HAOS bin dir was prepended to PATH for the current process.
                self.assertIn(str(haos_bin), os.environ.get("PATH", "").split(os.pathsep))

    def test_dsh_source_checkout_gets_node_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo = tmp_path / "search" / "deepseek-harness"
            (repo / "apps" / "cli" / "lib").mkdir(parents=True)
            (repo / "apps" / "cli" / "lib" / "bin.js").write_text("console.log('dsh');\n")

            haos_bin = tmp_path / ".haos" / "bin"
            with patch("shutil.which", return_value=None), \
                 patch.dict(os.environ, {"HAOS_HARNESS_SEARCH_DIRS": str(tmp_path / "search")}, clear=True), \
                 patch.object(Path, "home", classmethod(lambda cls: tmp_path)), \
                 patch.object(Path, "cwd", classmethod(lambda cls: tmp_path)), \
                 patch.object(harness_discovery, "_HAOS_BIN", haos_bin):
                registry = HarnessRegistry()
                info = registry.detect("dsh")
                self.assertTrue(info.available, f"expected dsh source discovery: {info}")
                self.assertIsNotNone(info.executable)
                assert info.executable is not None
                launcher = Path(info.executable)
                self.assertTrue(launcher.is_file())
                content = launcher.read_text(encoding="utf-8")
                self.assertIn("bin.js", content)
                self.assertIn("node", content)

    def test_missing_harness_warns_and_resolver_falls_back_to_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            empty = tmp_path / "empty"
            empty.mkdir()
            _reset_discovery()
            with patch("shutil.which", return_value=None), \
                 patch.dict(os.environ, {"HAOS_HARNESS_SEARCH_DIRS": str(empty)}, clear=True), \
                 patch.object(Path, "home", classmethod(lambda cls: tmp_path)), \
                 patch.object(Path, "cwd", classmethod(lambda cls: tmp_path)), \
                 patch.object(harness_discovery, "_HAOS_BIN", tmp_path / ".haos" / "bin"), \
                 self.assertLogs("hermes.platform.workers.harness_discovery", level="WARNING") as logs:
                registry = HarnessRegistry()
                info = registry.detect("dsh")
                self.assertFalse(info.available)
                self.assertEqual(info.details.get("fallback"), "native")
                self.assertTrue(any("not found" in line for line in logs.output))

                # The dispatcher-level resolver returns None => native worker path.
                spec = resolve_external_worker("dsh", {"task_id": "T-300", "title": "x"})
                self.assertIsNone(spec)
