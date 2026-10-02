"""Bounded, diagnosis-only operational AI subprocess.

This is tool isolation, NOT an OS sandbox: installed provider/plugin imports may
execute code and the worker retains profile credentials. Never treat its text or
recommendations as authorization. Call from the owning profile's runtime scope.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import threading
import time

MAX_IPC_BYTES = 64 * 1024
TREE_ROOT = Path(__file__).resolve().parents[3]


class InvestigatorError(RuntimeError):
    """Investigation failed; messages deliberately exclude provider logs/secrets."""


class InvestigatorTimeout(InvestigatorError):
    pass


class InvestigatorCancelled(InvestigatorError):
    pass


def decode_json(raw: bytes | str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def validate_diagnosis(value: dict, *, metadata: bool = True) -> dict:
    keys = {"summary", "findings"} | ({"usage", "model", "provider"} if metadata else set())
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("invalid diagnosis fields")
    if (not isinstance(value["summary"], str) or len(value["summary"]) > 2000
            or not isinstance(value["findings"], list) or len(value["findings"]) > 8):
        raise ValueError("invalid diagnosis types or bounds")
    for finding in value["findings"]:
        if not isinstance(finding, dict) or set(finding) != {"title", "cause", "confidence", "recommendations"}:
            raise ValueError("invalid finding fields")
        if not all(isinstance(finding[key], str) and len(finding[key]) <= bound
                   for key, bound in (("title", 200), ("cause", 2000))):
            raise ValueError("invalid finding text")
        confidence = finding["confidence"]
        if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError("invalid confidence")
        if (not isinstance(finding["recommendations"], list) or len(finding["recommendations"]) > 4
                or not all(isinstance(s, str) and len(s) <= 500 for s in finding["recommendations"])):
            raise ValueError("invalid recommendations")
    if metadata:
        if not all(isinstance(value[k], str) and len(value[k]) <= bound
                   for k, bound in (("model", 256), ("provider", 128))):
            raise ValueError("invalid runtime metadata")
        usage = value["usage"]
        counters = {"input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"}
        if usage != "unknown":
            if not isinstance(usage, dict) or set(usage) != counters | {"status"} or usage["status"] != "verified":
                raise ValueError("invalid usage")
            if not all(type(usage[k]) is int and usage[k] >= 0 for k in counters):
                raise ValueError("invalid counters")
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 32 * 1024:
        raise ValueError("diagnosis exceeds 32KiB")
    return value


def _kill_group(process):
    # A successful worker may leave descendants; reap its entire session too.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class Investigator:
    def __init__(self, timeout=90, max_output_tokens=2048):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if type(max_output_tokens) is not int or max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer")
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self._lock = threading.Lock()
        self._cancelled = threading.Event()

    def cancel(self):
        """Cancel the current invocation (poll latency at most 100ms)."""
        self._cancelled.set()

    def _command(self):
        return [sys.executable, "-m", "hermes.platform.agent_framework.investigator_worker"]

    def investigate(self, event: dict, telemetry: dict) -> dict:
        if not isinstance(event, dict) or not isinstance(telemetry, dict):
            raise ValueError("event and telemetry must be dictionaries")
        request = json.dumps({"event": event, "telemetry": telemetry,
                              "timeout": self.timeout, "max_output_tokens": self.max_output_tokens},
                             allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(request) > MAX_IPC_BYTES:
            raise ValueError("investigation request exceeds 64KiB")
        if os.name != "posix":
            raise InvestigatorError("process-group investigation requires POSIX")
        if not self._lock.acquire(blocking=False):
            raise InvestigatorError("investigator is already running")
        try:
            self._cancelled.clear()
            return self._run(request)
        finally:
            self._lock.release()

    def _run(self, request):
        from tools.environments.local import served_profile_child_env
        env = served_profile_child_env(inherit_credentials=True)
        # Kanban workers otherwise bypass an explicit empty toolset.
        env.pop("HERMES_KANBAN_TASK", None)
        deadline = time.monotonic() + self.timeout
        process = subprocess.Popen(self._command(), cwd=TREE_ROOT, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True,
                                   shell=False)
        output = bytearray()
        sent = 0
        try:
            with selectors.DefaultSelector() as selector:
                for pipe, mode in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ)):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, mode)
                while selector.get_map():
                    if self._cancelled.is_set():
                        raise InvestigatorCancelled("investigation cancelled")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise InvestigatorTimeout("investigation deadline exceeded")
                    for key, _ in selector.select(min(remaining, 0.1)):
                        if key.fileobj is process.stdin:
                            try:
                                sent += os.write(key.fd, request[sent:])
                            except BrokenPipeError:
                                sent = len(request)
                            if sent == len(request):
                                selector.unregister(process.stdin)
                                process.stdin.close()
                        else:
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(process.stdout)
                            else:
                                output.extend(chunk)
                                if len(output) > MAX_IPC_BYTES:
                                    raise InvestigatorError("investigation output exceeds 64KiB")
            while process.poll() is None:
                if self._cancelled.is_set():
                    raise InvestigatorCancelled("investigation cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InvestigatorTimeout("investigation deadline exceeded")
                try:
                    process.wait(timeout=min(remaining, 0.1))
                except subprocess.TimeoutExpired:
                    continue
            code = process.returncode
            if self._cancelled.is_set():
                raise InvestigatorCancelled("investigation cancelled")
            if code:
                raise InvestigatorError("investigation worker failed")
            try:
                return validate_diagnosis(decode_json(output))
            except (ValueError, TypeError, UnicodeError):
                raise InvestigatorError("invalid investigation response") from None
        finally:
            _kill_group(process)
            process.wait()
            for pipe in (process.stdin, process.stdout):
                if not pipe.closed:
                    pipe.close()
