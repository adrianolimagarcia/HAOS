"""Dedicated operational report artifact: no config.yaml, host tuning or shell."""
from __future__ import annotations

import base64
from pathlib import Path

from .approvals import canonical
from .state_manager import HAOSStateManager


class WorkspaceConfigUpdate:
    name = "workspace_config_update"

    def __init__(self, base_dir):
        self.state = HAOSStateManager(base_dir)
        # One fixed artifact keeps grants useful without granting arbitrary file access.
        self.target = self.state.base_dir / "artifacts" / "operational-report.json"

    def validate(self, step):
        if step.target != str(self.target) or set(step.params) != {"content"} or not isinstance(step.params["content"], dict):
            raise ValueError("Only the dedicated operational-report.json and content object are supported")
        self.state._check_path(self.target)
        rendered = canonical(step.params["content"]) + "\n"
        if len(rendered.encode()) > 65536:
            raise ValueError("Artifact exceeds 64 KiB")
        return rendered

    def capture(self, step):
        self.validate(step)
        if self.target.exists():
            raw = self.target.read_bytes()
            # The artifact is UTF-8; reject unsupported pre-state before any mutation.
            raw.decode("utf-8")
            if len(raw) > 65536:
                raise ValueError("Existing artifact exceeds 64 KiB")
            return {"exists": True, "bytes_b64": base64.b64encode(raw).decode()}
        return {"exists": False}

    def apply(self, step):
        self.state._write_text_atomic(self.target, self.validate(step))

    def verify(self, step):
        expected = self.validate(step).encode()
        return self.target.read_bytes() == expected

    def rollback(self, step, before):
        expected = self.validate(step).encode()
        current = self.capture(step)
        if current == before:
            return True
        if not self.target.exists() or self.target.read_bytes() != expected:
            raise RuntimeError("Artifact changed outside execution; refusing to overwrite unknown state")
        if before["exists"]:
            # Preserve exact bytes, including a non-JSON previous artifact.
            self.state._write_text_atomic(self.target, base64.b64decode(before["bytes_b64"]).decode("utf-8"))
        else:
            self.target.unlink(missing_ok=True)
            import os
            fd = os.open(self.target.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return self.capture(step) == before
