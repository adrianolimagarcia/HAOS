import json, os, subprocess, tempfile, unittest, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BIN = ROOT / "target" / "release" / "hermes-exec"


def call(root, params, *, extra_env=None):
    env = {**os.environ, "HERMES_EXEC_ROOT": str(root)}
    if extra_env:
        env.update(extra_env)
    p = subprocess.run(
        [str(BIN)], input=json.dumps({"id": 1, "method": "exec", "params": params}) + "\n",
        text=True, capture_output=True, env=env, check=False,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


@unittest.skipUnless(BIN.is_file(), "release binary not built")
class ProtocolTests(unittest.TestCase):
    def test_output_cap_under_flood(self):
        with tempfile.TemporaryDirectory() as d:
            result = call(d, {"argv": ["sh", "-c", "yes X | head -c 2000000"], "cwd": ".", "max_output_bytes": 1024})["result"]
            self.assertEqual(len(result["output"]), 1024)
            self.assertTrue(result["truncated"])
            self.assertEqual(result["returncode"], 0)

    def test_oversized_json_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run([str(BIN)], input='{"id":1,"method":"exec","params":{"command":"' + ("x" * (1024 * 1024)) + '"}}\n', text=True, capture_output=True, env={**os.environ, "HERMES_EXEC_ROOT": d}, check=False)
            self.assertEqual(p.returncode, 0)
            response = json.loads(p.stdout)
            self.assertEqual(response["error"]["code"], "request_too_large")

    def test_workspace_escape(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(call(d, {"command": "pwd", "cwd": ".."})["error"]["code"], "unsafe_cwd")

    def test_absolute_workspace_escape(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(call(d, {"command": "pwd", "cwd": "/"})["error"]["code"], "unsafe_cwd")

    def test_env_allowlist_with_argv(self):
        with tempfile.TemporaryDirectory() as d:
            result = call(d, {"argv": ["sh", "-c", "printf %s ${HERMES_SECRET-unset}"], "env": {"PATH": "/usr/bin"}})["result"]
            self.assertEqual(result["output"], "unset")

    def test_timeout_child_process(self):
        with tempfile.TemporaryDirectory() as d:
            result = call(d, {"argv": ["sh", "-c", "sleep 2 & wait"], "cwd": ".", "timeout_ms": 50})["result"]
            self.assertTrue(result["timed_out"])
            self.assertEqual(result["returncode"], 124)

    @unittest.skipUnless(sys.platform.startswith("linux"), "setsid process-group behavior is Linux-specific")
    def test_timeout_setsid_descendant_is_supported_only_on_linux(self):
        # This asserts the bounded timeout contract without claiming that a setsid escape
        # is killed; that stronger guarantee requires cgroup/proc-tree integration.
        with tempfile.TemporaryDirectory() as d:
            result = call(d, {"argv": ["sh", "-c", "setsid sh -c 'sleep 2' & wait"], "cwd": ".", "timeout_ms": 50})["result"]
            self.assertTrue(result["timed_out"])
            self.assertEqual(result["returncode"], 124)


if __name__ == "__main__":
    unittest.main()
