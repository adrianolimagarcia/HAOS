"""Versioned backfill and projection rebuild for the canonical Memory Fabric.

The migration is a *planner plus an applier*, and the planner is the product: a
dry run over a vault copy answers "what would this import?" without touching the
canonical journal, so a migration can be reviewed before it is committed.

What the plan pins, per note:

* **URI** — ``obsidian://<relative path>``, preserved verbatim in provenance so
  every canonical record can be traced back to the file a human can open.
* **Content hash** — the same normalized hash the canonical store dedupes on, so
  the plan's duplicate analysis matches what the store will actually do rather
  than approximating it.
* **Scope and ownership** — resolved up front from the caller's principal, and
  persisted with the record so a later read can re-authorize it.

GraphRAG is never written by the migrator. It is rebuilt by *replay*: the import
commits canonical records plus outbox jobs, and ``rebuild_projections`` drains
those jobs, which is the only path that can produce graph state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from hermes.platform.context.memory.access import MemoryAccessContext
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.projection_runner import ProjectionRunner
from hermes.platform.memory.vault_fts import parse_obsidian_frontmatter


@dataclass(frozen=True)
class MigrationEntry:
    """One planned (or applied) note import."""

    uri: str
    relative_path: str
    content_hash: str
    kind: str
    scope: str
    record_id: str
    owner_id: str
    valid_from: float
    duplicate_of: Optional[str] = None

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None


@dataclass
class MigrationReport:
    """Observable outcome of a dry run or a real import."""

    vault: str
    dry_run: bool
    notes_seen: int = 0
    decisions: int = 0
    imported: int = 0
    skipped_existing: int = 0
    skipped_projected: int = 0
    entries: List[MigrationEntry] = field(default_factory=list)
    scopes: Dict[str, int] = field(default_factory=dict)
    owners: Dict[str, int] = field(default_factory=dict)
    projections_replayed: int = 0

    @property
    def duplicates(self) -> List[MigrationEntry]:
        return [entry for entry in self.entries if entry.is_duplicate]

    @property
    def uris(self) -> List[str]:
        return [entry.uri for entry in self.entries]

    @property
    def content_hashes(self) -> List[str]:
        return [entry.content_hash for entry in self.entries]

    def summary(self) -> Dict[str, object]:
        return {
            "vault": self.vault,
            "dry_run": self.dry_run,
            "notes_seen": self.notes_seen,
            "decisions": self.decisions,
            "imported": self.imported,
            "skipped_existing": self.skipped_existing,
            "skipped_projected": self.skipped_projected,
            "duplicates": len(self.duplicates),
            "scopes": dict(self.scopes),
            "owners": dict(self.owners),
            "projections_replayed": self.projections_replayed,
        }


class MemoryMigrator:
    VERSION = 1

    def __init__(self, store: CanonicalMemoryStore, runner: ProjectionRunner) -> None:
        self.store = store
        self.runner = runner

    # -- planning ------------------------------------------------------------

    @staticmethod
    def _id(uri: str) -> str:
        return "legacy:" + hashlib.sha256(uri.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def content_hash(content: str) -> str:
        """The canonical store's own hash, so the plan cannot disagree with it."""
        return CanonicalMemoryStore._hash(content)

    @staticmethod
    def _kind_for(path: Path) -> str:
        return "decision" if "ADR" in path.name.upper() else "fact"

    @staticmethod
    def is_projection(content: str) -> bool:
        """True when a note was written by the fabric's Obsidian projection.

        ``fabric_committed`` is set by the projector and by nothing else, so it is
        a reliable marker that a file is derived state rather than source input.
        """
        front, _body = parse_obsidian_frontmatter(content)
        return str(front.get("fabric_committed", "")).strip().lower() in ("true", "1", "yes")

    def plan(
        self,
        vault_path: str | Path,
        *,
        default_scope: str = "project",
        access: Optional[MemoryAccessContext] = None,
        owner_id: str = "",
        skip_projected: bool = True,
    ) -> MigrationReport:
        """Build the import plan without writing anything.

        Duplicates are detected inside the plan itself (same normalized content
        in the same scope/kind), because the canonical store will collapse those
        to a single record — a plan that claimed otherwise would misreport the
        migration.

        ``skip_projected`` ignores notes the fabric itself wrote. The Obsidian
        projection targets the same vault a migration reads from, so without this
        a second run would import its own projections back as new facts.
        """
        vault = Path(vault_path)
        if not vault.is_dir():
            raise ValueError("vault path is not a directory: %s" % vault)
        report = MigrationReport(vault=str(vault), dry_run=True)
        seen_content: Dict[Tuple[str, str, str], str] = {}
        owner = owner_id or (access.principal_id if access is not None else "")
        for path in sorted(vault.rglob("*.md")):
            if not path.is_file():
                continue
            relative = str(path.relative_to(vault))
            uri = "obsidian://" + relative
            content = path.read_text(encoding="utf-8")
            if skip_projected and self.is_projection(content):
                report.skipped_projected += 1
                continue
            digest = self.content_hash(content)
            kind = self._kind_for(path)
            duplicate_of = seen_content.get((default_scope, kind, digest))
            entry = MigrationEntry(
                uri=uri,
                relative_path=relative,
                content_hash=digest,
                kind=kind,
                scope=default_scope,
                record_id=self._id(uri),
                owner_id=owner,
                valid_from=path.stat().st_mtime,
                duplicate_of=duplicate_of,
            )
            seen_content[(default_scope, kind, digest)] = uri
            report.entries.append(entry)
            report.notes_seen += 1
            if kind == "decision":
                report.decisions += 1
            report.scopes[default_scope] = report.scopes.get(default_scope, 0) + 1
            report.owners[owner] = report.owners.get(owner, 0) + 1
        return report

    # -- applying ------------------------------------------------------------

    def backfill_obsidian(
        self,
        vault_path: str | Path,
        default_scope: str = "project",
        *,
        access: Optional[MemoryAccessContext] = None,
        owner_id: str = "",
        dry_run: bool = False,
        rebuild: bool = True,
    ) -> MigrationReport:
        """Import vault notes into the canonical journal.

        ``dry_run=True`` returns the plan and writes nothing — neither canonical
        records nor projections. ``rebuild`` drains the outbox afterwards, which
        is how GraphRAG and the vector index are reconstructed: by replay, never
        by the migrator writing to them.
        """
        report = self.plan(
            vault_path, default_scope=default_scope, access=access, owner_id=owner_id
        )
        if dry_run:
            return report
        report.dry_run = False

        for entry in report.entries:
            if entry.is_duplicate:
                # Canonical dedupe will resolve this to the first record; count it
                # as an expected duplicate rather than a second import.
                report.skipped_existing += 1
                continue
            metadata: Dict[str, object] = {
                "legacy_uri": entry.uri,
                "migration_version": self.VERSION,
            }
            if access is not None:
                metadata.update(access.write_metadata(entry.scope))
            elif entry.owner_id:
                metadata["owner_id"] = entry.owner_id
            existing = self.store.get(entry.record_id)
            if existing is not None:
                report.skipped_existing += 1
                continue
            self.store.append(
                content=Path(str(vault_path), entry.relative_path).read_text(encoding="utf-8"),
                scope=entry.scope,
                kind=entry.kind,
                provenance=({"uri": entry.uri, "migration_version": self.VERSION},),
                metadata=metadata,
                idempotency_key=entry.record_id,
                valid_from=entry.valid_from,
            )
            report.imported += 1

        if rebuild:
            report.projections_replayed = self.rebuild_projections()
        return report

    def rebuild_projections(self) -> int:
        """Replay every outstanding projection job; safe to call repeatedly."""
        total = 0
        while True:
            applied = self.runner.drain(limit_per_projection=128)
            total += applied
            if not applied:
                return total
