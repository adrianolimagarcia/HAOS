"""Persistent HAOS agent hierarchy for the standalone Control Plane.

This is organizational state, deliberately separate from mission task dependencies.
It models a rooted master, manager nodes, shared bot model policy, and optional
manager-to-manager advisory edges with a bounded discussion budget.
"""
from __future__ import annotations

import json
import fcntl
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

_ALLOWED_ROLES = {"master", "manager", "bot"}


class HierarchyError(ValueError):
    """Invalid hierarchy mutation."""


class AgentHierarchyStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = self._load()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "version": 1,
            "bot_model": {"provider": "", "model": ""},
            "nodes": [],
            "advisory_edges": [],
            "councils": [],
            "discussion_limits": {"default_turns": 3, "max_turns": 20},
        }

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                base = self._empty()
                base.update(raw)
                return base
        except (OSError, ValueError):
            pass
        return self._empty()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            fd, tmp = tempfile.mkstemp(prefix=".agent-hierarchy.", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, ensure_ascii=False, indent=2, sort_keys=True)
                    fh.write("\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            finally:
                try:
                    os.unlink(tmp)
                except FileNotFoundError:
                    pass
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def _node(self, node_id: str) -> dict[str, Any]:
        for node in self._data["nodes"]:
            if node["id"] == node_id:
                return node
        raise HierarchyError(f"unknown agent: {node_id}")

    def _validate(self) -> None:
        nodes = self._data["nodes"]
        ids = [n.get("id") for n in nodes]
        if len(ids) != len(set(ids)):
            raise HierarchyError("duplicate agent id")
        masters = [n for n in nodes if n.get("role") == "master"]
        if len(masters) > 1:
            raise HierarchyError("hierarchy may have at most one master")
        known = set(ids)
        for node in nodes:
            role = node.get("role")
            parent = node.get("parent_id")
            if role not in _ALLOWED_ROLES:
                raise HierarchyError("role must be master, manager, or bot")
            if role == "master" and parent:
                raise HierarchyError("master cannot have a parent")
            if role == "manager" and parent and self._node(parent)["role"] != "master":
                raise HierarchyError("manager must report to master")
            if role == "bot" and parent and self._node(parent)["role"] != "manager":
                raise HierarchyError("bot must report to manager")
            if role != "master" and parent not in known:
                raise HierarchyError("non-master agent requires an existing parent")
            if parent and parent == node["id"]:
                raise HierarchyError("agent cannot parent itself")
        # parent chains must be acyclic
        for node in nodes:
            seen = set()
            cur = node
            while cur.get("parent_id"):
                if cur["id"] in seen:
                    raise HierarchyError("parent hierarchy contains a cycle")
                seen.add(cur["id"])
                cur = self._node(cur["parent_id"])
        for edge in self._data["advisory_edges"]:
            a, b = edge.get("from_id"), edge.get("to_id")
            if a == b or a not in known or b not in known:
                raise HierarchyError("invalid advisory edge")
            if self._node(a)["role"] != "manager" or self._node(b)["role"] != "manager":
                raise HierarchyError("advisory edges must connect managers")
            turns = int(edge.get("max_turns", 0))
            if turns < 1 or turns > int(self._data["discussion_limits"]["max_turns"]):
                raise HierarchyError("max_turns is outside the configured limit")

    def set_bot_model(self, provider: str, model: str) -> dict[str, Any]:
        with self._lock:
            self._data["bot_model"] = {"provider": str(provider).strip(), "model": str(model).strip()}
            self._save()
            return dict(self._data["bot_model"])

    def upsert_node(self, payload: dict[str, Any], node_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            ident = node_id or str(payload.get("id") or uuid.uuid4().hex[:12])
            node = {
                "id": ident,
                "name": str(payload.get("name") or ident).strip(),
                "role": str(payload.get("role") or "bot").strip().lower(),
                "parent_id": payload.get("parent_id") or None,
                "provider": str(payload.get("provider") or "").strip(),
                "model": str(payload.get("model") or "").strip(),
                "profile": str(payload.get("profile") or ident).strip(),
                "description": str(payload.get("description") or "").strip(),
                "enabled": bool(payload.get("enabled", True)),
            }
            old = next((n for n in self._data["nodes"] if n["id"] == ident), None)
            old_copy = dict(old) if old else None
            if old:
                old.update(node)
            else:
                self._data["nodes"].append(node)
            try:
                self._validate()
            except Exception:
                if old is not None:
                    old.clear(); old.update(old_copy or {})
                else:
                    self._data["nodes"].pop()
                raise
            self._save()
            return dict(node)

    def delete_node(self, node_id: str) -> None:
        with self._lock:
            self._node(node_id)
            descendants = {node_id}
            changed = True
            while changed:
                changed = False
                for n in self._data["nodes"]:
                    if n.get("parent_id") in descendants and n["id"] not in descendants:
                        descendants.add(n["id"]); changed = True
            self._data["nodes"] = [n for n in self._data["nodes"] if n["id"] not in descendants]
            self._data["advisory_edges"] = [e for e in self._data["advisory_edges"] if e["from_id"] not in descendants and e["to_id"] not in descendants]
            self._save()

    def set_advisory_edge(self, from_id: str, to_id: str, max_turns: int) -> dict[str, Any]:
        with self._lock:
            edge = {"from_id": from_id, "to_id": to_id, "max_turns": int(max_turns)}
            old_edges = list(self._data["advisory_edges"])
            self._data["advisory_edges"] = [e for e in old_edges if not (e["from_id"] == from_id and e["to_id"] == to_id)]
            self._data["advisory_edges"].append(edge)
            try:
                self._validate()
            except Exception:
                self._data["advisory_edges"] = old_edges
                raise
            self._save()
            return dict(edge)

    def start_council(self, from_id: str, to_id: str, topic: str, max_turns: int | None = None) -> dict[str, Any]:
        """Open a durable, bounded advisory discussion between two managers."""
        with self._lock:
            edge = next((e for e in self._data["advisory_edges"]
                         if e["from_id"] == from_id and e["to_id"] == to_id), None)
            if edge is None:
                edge = next((e for e in self._data["advisory_edges"]
                             if e["from_id"] == to_id and e["to_id"] == from_id), None)
            if edge is None:
                raise HierarchyError("no advisory relationship between managers")
            turns = int(max_turns if max_turns is not None else edge["max_turns"])
            limit = int(self._data["discussion_limits"]["max_turns"])
            if turns < 1 or turns > limit:
                raise HierarchyError("council turn limit is outside configured bounds")
            session = {"id": uuid.uuid4().hex, "from_id": from_id, "to_id": to_id,
                       "topic": str(topic).strip(), "max_turns": turns, "turns_used": 0,
                       "status": "open", "messages": []}
            if not session["topic"]:
                raise HierarchyError("council topic is required")
            self._data.setdefault("councils", []).append(session)
            self._save()
            return dict(session)

    def append_council_turn(self, council_id: str, speaker_id: str, message: str) -> dict[str, Any]:
        """Consume exactly one turn; turns beyond the durable budget are rejected."""
        with self._lock:
            session = next((s for s in self._data.get("councils", []) if s["id"] == council_id), None)
            if session is None:
                raise HierarchyError("unknown council")
            if session["status"] == "exhausted" and int(session["turns_used"]) >= int(session["max_turns"]):
                raise HierarchyError("council turn budget exhausted")
            if session["status"] != "open":
                raise HierarchyError("council is not open")
            if speaker_id not in (session["from_id"], session["to_id"]):
                raise HierarchyError("speaker is not a council member")
            if not str(message).strip():
                raise HierarchyError("council message is required")
            if int(session["turns_used"]) >= int(session["max_turns"]):
                session["status"] = "exhausted"
                self._save()
                raise HierarchyError("council turn budget exhausted")
            session["messages"].append({"speaker_id": speaker_id, "message": str(message), "turn": session["turns_used"] + 1})
            session["turns_used"] += 1
            if session["turns_used"] >= session["max_turns"]:
                session["status"] = "exhausted"
            self._save()
            return dict(session)

