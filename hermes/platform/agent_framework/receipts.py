"""Durable execution intent and evidence, deliberately not automatic crash replay."""
from __future__ import annotations

import json

from .approvals import plan_binding
from .state_manager import HAOSStateManager


class ReceiptStore:
    def __init__(self, base_dir):
        self.state = HAOSStateManager(base_dir)
        self.directory = self.state.base_dir / "receipts"

    def path(self, plan):
        if not self.state._safe_name(plan.plan_id):
            raise ValueError("Unsafe plan_id")
        return self.directory / f"{plan.plan_id}.json"

    def begin(self, plan):
        path = self.path(plan)
        self.state._check_path(path)
        # Never overwrite even a finished receipt: plan IDs are one-use.
        if path.exists():
            raise RuntimeError("Plan already attempted; inspect receipt (interrupted plans require operator recovery)")
        if self.directory.exists():
            for previous in self.directory.glob("*.json"):
                self.state._check_path(previous)
                data = json.loads(previous.read_text())
                if data.get("phase") not in {"finished", "rolled_back"}:
                    raise RuntimeError("Interrupted/partial execution requires operator recovery; no new mutation allowed")
        data = {"plan_id": plan.plan_id, "plan_digest": plan_binding(plan), "phase": "started", "steps": []}
        self.save(plan, data)
        return data

    def save(self, plan, data):
        self.state._write_json_file_atomic(self.path(plan), data)
