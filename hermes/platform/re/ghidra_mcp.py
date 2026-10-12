"""Ghidra Headless MCP client for HAOS Platform (Phase 3).

Exposes deep reverse-engineering and decompilation via GhidraMCP.
Enforces character budget and spill-to-file semantics for decompiled C outputs.
Implements asymmetric timeout handling (abort on timeout for heavy analysis).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, Optional
import urllib.error
import urllib.request

from hermes.platform.mcp.spill import apply_mcp_result_budget, get_mcp_result_budget
from hermes_constants import get_hermes_home


class GhidraMCPError(RuntimeError):
    """Raised when Ghidra headless MCP operation fails."""
    pass


class GhidraMCPClient:
    """Manages Ghidra headless MCP daemon lifecycle and client calls."""

    def __init__(
        self,
        binary_path: str,
        ghidra_install_dir: str = "/opt/ghidra",
        extension_jar: Optional[str] = None,
        port: int = 8089,
        host: str = "127.0.0.1",
        timeout_seconds: float = 60.0,
        venv_python: Optional[str] = None,
    ) -> None:
        self.binary_path = os.path.abspath(binary_path)
        self.ghidra_install_dir = ghidra_install_dir
        self.extension_jar = extension_jar or "/opt/ghidra/Extensions/Ghidra/GhidraMCP/lib/GhidraMCP.jar"
        self.port = port
        self.host = host
        self.timeout_seconds = timeout_seconds
        self.base_url = f"http://{host}:{port}"
        self._proc: Optional[subprocess.Popen[str]] = None

        if venv_python:
            self._python_bin = venv_python
        else:
            local_venv = Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python"
            if local_venv.exists():
                self._python_bin = str(local_venv)
            else:
                self._python_bin = "python3"

    @property
    def ghidra_mcp_bin(self) -> str:
        venv_bin = Path(self._python_bin).parent / "ghidra-mcp"
        if venv_bin.exists():
            return str(venv_bin)
        return "ghidra-mcp"

    def is_available(self) -> bool:
        """Check if Ghidra install and GhidraMCP extension exist."""
        return (
            os.path.isdir(self.ghidra_install_dir)
            and os.path.exists(self.extension_jar)
            and Path(self.ghidra_mcp_bin).exists()
        )

    def start_daemon(self, analyze: bool = True, wait_ready_seconds: float = 25.0) -> None:
        """Start GhidraMCP headless background daemon."""
        if self._proc is not None and self._proc.poll() is None:
            return

        cmd = [
            self.ghidra_mcp_bin,
            "--headless",
            "--binary", self.binary_path,
            "--ghidra-install-dir", self.ghidra_install_dir,
            "--extension-jar", self.extension_jar,
            "--transport", "streamable-http",
            "--mcp-host", self.host,
            "--mcp-port", str(self.port),
            "--ready-timeout", str(int(wait_ready_seconds)),
        ]
        if not analyze:
            cmd.append("--no-analyze")

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait until endpoint is responsive
        start_time = time.time()
        connected = False
        while time.time() - start_time < wait_ready_seconds:
            if self._proc.poll() is not None:
                _, err = self._proc.communicate()
                raise GhidraMCPError(f"Ghidra daemon terminated prematurely: {err}")
            try:
                req = urllib.request.Request(f"{self.base_url}/mcp", method="GET")
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status in (200, 404, 405):  # HTTP endpoint up
                        connected = True
                        break
            except Exception:
                time.sleep(0.5)

        if not connected:
            self.stop_daemon()
            raise GhidraMCPError(f"Timed out waiting for GhidraMCP on port {self.port}")

    def stop_daemon(self) -> None:
        """Terminate the Ghidra headless daemon."""
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5.0)
            except Exception:
                self._proc.kill()
            finally:
                self._proc = None

    def __enter__(self) -> GhidraMCPClient:
        self.start_daemon()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop_daemon()

    def decompile_function(self, function_name_or_address: str, budget: Optional[int] = None) -> str:
        """Decompile a function with asymmetric timeout and spill-to-file protection."""
        payload: Dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "decompile_function",
                "arguments": {"name": function_name_or_address}
            }
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/mcp",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                result_content = ""
                if "result" in res_json and "content" in res_json["result"]:
                    for item in res_json["result"]["content"]:
                        if item.get("type") == "text":
                            result_content += item.get("text", "")
                elif "error" in res_json:
                    raise GhidraMCPError(f"Ghidra tool error: {res_json['error']}")
                else:
                    result_content = str(res_json)
        except urllib.error.URLError as e:
            raise GhidraMCPError(f"Failed to communicate with GhidraMCP: {e}")

        effective_budget = budget if budget is not None else get_mcp_result_budget()
        return apply_mcp_result_budget(
            result_content,
            tool_name="ghidra_decompile_function",
            budget=effective_budget,
        )
