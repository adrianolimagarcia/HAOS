"""Versioned backfill and projection rebuild helpers for Memory Fabric."""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.projection_runner import ProjectionRunner

class MemoryMigrator:
    VERSION = 1

    def __init__(self, store: CanonicalMemoryStore, runner: ProjectionRunner) -> None:
        self.store = store
        self.runner = runner

    @staticmethod
    def _id(uri: str) -> str:
        return "legacy:" + hashlib.sha256(uri.encode("utf-8")).hexdigest()[:24]

    def backfill_obsidian(self, vault_path: str | Path, default_scope: str = "project") -> int:
        vault = Path(vault_path)
        imported = 0
        for path in sorted(vault.rglob("*.md")):
            uri = "obsidian://" + str(path.relative_to(vault))
            content = path.read_text(encoding="utf-8")
            record = self.store.append(
                content=content, scope=default_scope,
                kind="decision" if "ADR" in path.name.upper() else "fact",
                provenance=({"uri": uri, "migration_version": self.VERSION},),
                metadata={"legacy_uri": uri, "migration_version": self.VERSION},
                idempotency_key=self._id(uri),
                valid_from=path.stat().st_mtime,
            )
            if record.record_id == self._id(uri):
                imported += 1
        return imported

    def rebuild_projections(self) -> int:
        """Replay every outstanding projection job; safe to call repeatedly."""
        total = 0
        while True:
            applied = self.runner.drain(limit_per_projection=128)
            total += applied
            if not applied:
                return total
