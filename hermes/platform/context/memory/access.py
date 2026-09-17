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
        """Scopes this principal can hold data in, derived from membership.

        Declaring ``team``/``project`` for a principal with no membership would
        widen every candidate query, so the list mirrors ``can_read`` exactly.
        """
        scopes = ["private"]
        if self.team_ids:
            scopes.append("team")
        if self.project_ids:
            scopes.append("project")
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

    def write_metadata(self, scope: str) -> dict:
        """Tenancy metadata a write must carry to satisfy ``require_write``.

        Persisting this alongside the record is what makes a later read
        re-authorizable: the ACL travels with the fact, not with the process.
        """
        if scope == "private":
            return {"owner_id": self.principal_id}
        if scope == "team":
            return {"team_ids": sorted(self.team_ids)}
        if scope == "project":
            return {"project_ids": sorted(self.project_ids)}
        return {}

    def require_write(self, scope: str, metadata: dict) -> None:
        if not self.can_read(scope, metadata):
            raise PermissionError("Memory scope write denied: %s" % scope)
