"""HAOS ADR Registry Contract.

Regression guard for the graft in `06b52e9ab6`, which landed four independent ADR series
(each starting its own `ADR-001`) in one commit — four files named `ADR-001-*.md` and two
different documents claiming `ADR-012`.

These tests assert *relationships* between the registry and the documents, not the current
file list: a new ADR must be registered, canonical IDs must stay unique, and cross-references
must resolve inside their own namespace.

The registry is `architecture/ADRs/INDEX.md`; the two canonical namespaces are
`ADR-NNN` (platform, `docs/architecture/`) and `GOV-NNN` (governance, `architecture/ADRs/`).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM_DIR = REPO_ROOT / "docs" / "architecture"
GOVERNANCE_DIR = REPO_ROOT / "architecture" / "ADRs"
PARALLEL_GUILD_DIR = GOVERNANCE_DIR / "parallel" / "guild"
INDEX_FILE = GOVERNANCE_DIR / "INDEX.md"

# `ADR-001` / `GOV-016`, as they appear in filenames, H1 titles and `Governed By:` lines.
REFERENCE_RE = re.compile(r"\b(ADR|GOV)-(\d{3})\b")

# A registration row: `| ADR-001 | `docs/architecture/ADR-001-...md` | ... |`
REGISTRY_ROW_RE = re.compile(r"^\|\s*(ADR|GOV)-(\d{3})\s*\|\s*`([^`]+)`")
# A Guild mapping row: `| `parallel/guild/GUILD-001-...md` | GOV-001 | ... |`
GUILD_ROW_RE = re.compile(r"^\|\s*`(parallel/guild/[^`]+)`")


def _canonical_files(directory: Path, prefix: str) -> dict[str, Path]:
    """Map every canonical ID in `directory` to its file."""
    ids: dict[str, Path] = {}
    for path in sorted(directory.glob(f"{prefix}-*.md")):
        match = re.match(rf"{prefix}-(\d{{3}})-", path.name)
        if match:
            ids[f"{prefix}-{match.group(1)}"] = path
    return ids


class TestCanonicalIdUniqueness(unittest.TestCase):
    """The bug being guarded: the same ID resolving to more than one document."""

    def test_platform_ids_are_unique(self) -> None:
        paths = sorted(PLATFORM_DIR.glob("ADR-*.md"))
        numbers = [m.group(1) for p in paths if (m := re.match(r"ADR-(\d{3})-", p.name))]
        self.assertEqual(
            len(numbers),
            len(set(numbers)),
            f"duplicate ADR-NNN in {PLATFORM_DIR}: {sorted(numbers)}",
        )

    def test_governance_ids_are_unique(self) -> None:
        paths = sorted(GOVERNANCE_DIR.glob("GOV-*.md"))
        numbers = [m.group(1) for p in paths if (m := re.match(r"GOV-(\d{3})-", p.name))]
        self.assertEqual(
            len(numbers),
            len(set(numbers)),
            f"duplicate GOV-NNN in {GOVERNANCE_DIR}: {sorted(numbers)}",
        )

    def test_no_bare_adr_file_in_governance_namespace(self) -> None:
        """Guild's parallel set must not sit at the top level of the governance namespace."""
        strays = sorted(p.name for p in GOVERNANCE_DIR.glob("ADR-*.md"))
        self.assertEqual(
            strays,
            [],
            f"bare ADR-* files in {GOVERNANCE_DIR} collide with the platform namespace: {strays}",
        )

    def test_no_gov_file_in_platform_namespace(self) -> None:
        strays = sorted(p.name for p in PLATFORM_DIR.glob("GOV-*.md"))
        self.assertEqual(strays, [], f"GOV-* files in {PLATFORM_DIR}: {strays}")


class TestRegistryCompleteness(unittest.TestCase):
    """Every canonical document must be registered; the registry must not invent documents."""

    def test_index_exists(self) -> None:
        self.assertTrue(INDEX_FILE.is_file(), f"ADR registry missing at {INDEX_FILE}")

    def test_every_canonical_document_is_registered(self) -> None:
        index = INDEX_FILE.read_text(encoding="utf-8")
        unregistered = [
            path.name
            for ids in (
                _canonical_files(PLATFORM_DIR, "ADR"),
                _canonical_files(GOVERNANCE_DIR, "GOV"),
            )
            for path in ids.values()
            if path.name not in index
        ]
        self.assertEqual(
            unregistered,
            [],
            f"canonical ADRs missing from {INDEX_FILE.name}: {unregistered}",
        )

    def test_registry_rows_point_to_the_document_they_name(self) -> None:
        """Each registry row must name an existing file whose filename carries that ID.

        Only table rows count as registrations — prose in the index may legitimately mention
        IDs that are not documents (fixture IDs, the runtime seed ADR, upstream `docs/ADR.md`).
        """
        broken: list[str] = []
        for raw in INDEX_FILE.read_text(encoding="utf-8").splitlines():
            row = REGISTRY_ROW_RE.match(raw)
            if row:
                ident, target = f"{row.group(1)}-{row.group(2)}", row.group(3)
                path = REPO_ROOT / target if target.startswith("docs/") else GOVERNANCE_DIR / target
                if not path.is_file():
                    broken.append(f"{ident} -> {target} (missing)")
                elif not path.name.startswith(f"{ident}-"):
                    broken.append(f"{ident} -> {target} (filename declares a different ID)")
            guild = GUILD_ROW_RE.match(raw)
            if guild:
                target = guild.group(1)
                if not (GOVERNANCE_DIR / target).is_file():
                    broken.append(f"guild row -> {target} (missing)")
        self.assertEqual(broken, [], f"{INDEX_FILE.name} has broken registry rows: {broken}")


class TestCrossReferencesResolve(unittest.TestCase):
    """A citation must resolve inside its own namespace, or the numbering is ambiguous again."""

    def test_platform_documents_only_cite_platform_ids(self) -> None:
        available = set(_canonical_files(PLATFORM_DIR, "ADR"))
        dangling: list[str] = []
        for path in sorted(PLATFORM_DIR.glob("ADR-*.md")):
            for match in REFERENCE_RE.finditer(path.read_text(encoding="utf-8")):
                if match.group(1) != "ADR":
                    continue
                if match.group(0) not in available:
                    dangling.append(f"{path.name}: {match.group(0)}")
        self.assertEqual(dangling, [], f"platform ADRs cite unregistered ADR ids: {dangling}")

    def test_governance_documents_only_cite_governance_ids(self) -> None:
        available = set(_canonical_files(GOVERNANCE_DIR, "GOV"))
        dangling: list[str] = []
        for path in sorted(GOVERNANCE_DIR.glob("GOV-*.md")):
            for match in REFERENCE_RE.finditer(path.read_text(encoding="utf-8")):
                if match.group(1) != "GOV":
                    continue
                if match.group(0) not in available:
                    dangling.append(f"{path.name}: {match.group(0)}")
        self.assertEqual(dangling, [], f"governance ADRs cite unregistered GOV ids: {dangling}")


class TestGuildParallelSeries(unittest.TestCase):
    """Guild is preserved but must stay visibly non-canonical."""

    def test_guild_documents_are_marked_parallel(self) -> None:
        index = INDEX_FILE.read_text(encoding="utf-8")
        paths = sorted(PARALLEL_GUILD_DIR.glob("GUILD-*.md"))
        # Guard against a vacuous pass: if this namespace is ever renamed again, the glob has to
        # move with it instead of quietly matching nothing.
        self.assertTrue(paths, f"no GUILD-*.md found in {PARALLEL_GUILD_DIR}")
        for path in paths:
            content = path.read_text(encoding="utf-8")
            self.assertIn("parallel", content, f"{path.name} does not declare itself parallel")
            self.assertIn(path.name, index, f"{path.name} is not mapped in {INDEX_FILE.name}")

    def test_no_adr_prefixed_file_in_guild_namespace(self) -> None:
        """`ADR-NNN` means the platform series; the Guild set must not reuse that prefix."""
        strays = sorted(p.name for p in PARALLEL_GUILD_DIR.glob("ADR-*.md"))
        self.assertEqual(
            strays,
            [],
            f"ADR-* files in {PARALLEL_GUILD_DIR} make `ADR-NNN` ambiguous again: {strays}",
        )


if __name__ == "__main__":
    unittest.main()
