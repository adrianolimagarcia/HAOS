"""Radare2 MCP Backbone client for HAOS Platform.

Manages headless radare2 instance via the r2mcp MCP server protocol.
Enforces character budget and spillover to file for voluminous disassembly/decompilation outputs.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes.platform.mcp.spill import apply_mcp_result_budget, get_mcp_result_budget

logger = logging.getLogger("hermes.platform.re.r2_backbone")


class Radare2MCPClient:
    """Headless MCP client for radare2 (via r2mcp executable)."""

    def __init__(
        self,
        r2mcp_path: Optional[str] = None,
        budget: Optional[int] = None,
        timeout: float = 30.0,
    ) -> None:
        self.r2mcp_path = r2mcp_path or os.environ.get("R2MCP_PATH") or shutil.which("r2mcp") or "/usr/local/bin/r2mcp"
        self.budget = budget if budget is not None else get_mcp_result_budget()
        self.timeout = timeout
        self._proc: Optional[subprocess.Popen[str]] = None
        self._msg_id = 0
        self._initialized = False

    def is_available(self) -> bool:
        """Check if r2mcp binary is available on the system."""
        return os.path.isfile(self.r2mcp_path) and os.access(self.r2mcp_path, os.X_OK)

    def start(self) -> None:
        """Start the r2mcp process and perform MCP initialization handshake."""
        if not self.is_available():
            raise FileNotFoundError(f"r2mcp binary not found or not executable at '{self.r2mcp_path}'")

        if self._proc is not None and self._proc.poll() is None:
            return  # already running

        self._proc = subprocess.Popen(
            [self.r2mcp_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # 1. Initialize
        init_res = self._send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "haos-re-client", "version": "1.0"},
            },
        )
        if "error" in init_res:
            raise RuntimeError(f"MCP initialize error: {init_res['error']}")

        # 2. Initialized notification
        self._send_notification("notifications/initialized", {})
        self._initialized = True

    def close(self) -> None:
        """Terminate the r2mcp process."""
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            finally:
                self._proc = None
                self._initialized = False

    def __enter__(self) -> Radare2MCPClient:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise RuntimeError("r2mcp process not running")

        req_id = self._next_id()
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params

        line = json.dumps(payload) + "\n"
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

        resp_line = self._proc.stdout.readline()
        if not resp_line:
            stderr_out = ""
            if self._proc.stderr:
                try:
                    stderr_out = self._proc.stderr.read()
                except Exception:
                    pass
            raise EOFError(f"r2mcp closed stdout unexpectedly. Stderr: {stderr_out}")

        return json.loads(resp_line)

    def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("r2mcp process not running")

        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params

        line = json.dumps(payload) + "\n"
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, budget: Optional[int] = None) -> str:
        """Call an MCP tool and apply character budget + spill-to-file logic."""
        if not self._initialized:
            self.start()

        args = arguments or {}
        resp = self._send_request("tools/call", {"name": name, "arguments": args})

        if "error" in resp:
            err = resp["error"]
            raise RuntimeError(f"Tool '{name}' failed: {err.get('message', str(err))}")

        result_obj = resp.get("result", {})
        content_items = result_obj.get("content", [])
        raw_text_parts: List[str] = []

        for item in content_items:
            if isinstance(item, dict) and "text" in item:
                raw_text_parts.append(item["text"])

        full_output = "\n".join(raw_text_parts)

        # Apply spill-to-file budget enforcement
        effective_budget = budget if budget is not None else self.budget
        spilled_output = apply_mcp_result_budget(
            full_output,
            tool_name=f"r2_{name}",
            budget=effective_budget,
        )
        return spilled_output

    def open_file(self, file_path: str) -> str:
        """Open binary target in radare2."""
        abs_path = os.path.abspath(file_path)
        if not os.path.isfile(abs_path):
            raise FileNotFoundError(f"Binary file not found: {abs_path}")
        return self.call_tool("open_file", {"file_path": abs_path})

    def analyze(self) -> str:
        """Run standard binary analysis (aa)."""
        return self.call_tool("analyze", {})

    def list_functions(self, budget: Optional[int] = None) -> str:
        """List functions discovered during analysis."""
        return self.call_tool("list_functions", {}, budget=budget)

    def list_imports(self) -> str:
        """List imported symbols."""
        return self.call_tool("list_imports", {})

    def list_exports(self) -> str:
        """List exported symbols."""
        return self.call_tool("list_exports", {})

    def list_sections(self) -> str:
        """List sections and segments."""
        return self.call_tool("list_sections", {})

    def disassemble_function(self, address_or_name: str, budget: Optional[int] = None) -> str:
        """Disassemble function at address or name."""
        return self.call_tool("disassemble_function", {"address": address_or_name}, budget=budget)

    def decompile_function(self, address_or_name: str, budget: Optional[int] = None) -> str:
        """Decompile function at address or name to C pseudocode."""
        return self.call_tool("decompile_function", {"address": address_or_name}, budget=budget)
