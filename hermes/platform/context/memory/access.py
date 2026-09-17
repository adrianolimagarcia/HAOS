"""Authoritative scope and principal checks for Memory Fabric."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable

@dataclass(frozen=True)
class MemoryAccessContext:
    principal_id: str
    team_ids: FrozenSet[str] = field(default_factory=frozenset)
    project_ids: FrozenSet[str] = field(default_factory=frozenset)
    allow_global: bool = True

    def allowed_scopes(self) -> tuple[str, ...]:
        scopes = ["private", "team", "project"]
        if self.allow_global:
            scopes.append("global")
        return tuple(scopes)

    def can_read(self, scope: str, metadata: dict) -> bool:
        if scope == "global":
            return self.allow_global
        if scope == "private":
            return metadata.get("owner_id") == self.principal_id
        if scope == "team":
            return bool(set(metadata.get("team_ids", ())) & set(self.team_ids))
        if scope == "project":
            return bool(set(metadata.get("project_ids", ())) & set(self.project_ids))
        return False

    def require_write(self, scope: str, metadata: dict) -> None:
        if not self.can_read(scope, metadata):
            raise PermissionError("Memory scope write denied: %s" % scope)
