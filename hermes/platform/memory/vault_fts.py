"""VaultFTSIndex — SQLite FTS5 (trigram) substring index over an Obsidian vault.

WHY this module exists
----------------------
Both Obsidian adapters used to answer a substring query by ``rglob("*.md")`` +
reading + parsing **every** markdown note on every call: O(vault) file I/O per
query, repeated each retrieve/search. This module is the shared *derived*
index that replaces that scan. Two design constraints shape everything below:

1. **Exact-result contract.** The observable behaviour of ``ObsidianAdapter``
   (both the fabric ``retrieve`` and the B3 ``search``) must not change: same
   result set, case-insensitive substring over the whole raw note text (plus,
   for the fabric adapter only, the note *title* = frontmatter ``title`` or the
   file stem). The index therefore stores a shadow ``notes`` table with the
   exact text the old matcher scanned, plus an FTS5 ``trigram`` table so real
   substring search is index-speed instead of a full-table LIKE.
2. **Incremental refresh by mtime.** ``sync()`` walks the vault collecting only
   ``(relative_path, st_mtime_ns)`` and compares against the shadow table, so a
   repeated query re-reads only notes that were added/changed/removed since the
   last sync — not every file.

Why trigram + an exactness gate
-------------------------------
The repo's session search already uses ``trigram`` for substring semantics, and
we verified empirically that the SQLite trigram tokenizer indexes every
consecutive 3-char window of the text with no separator handling (mid-word
substrings, ``ADR-018``, ``!!!``, spaces all match). Two properties of trigram
are **not** guaranteed across SQLite builds, so we only use the FTS fast path
when they cannot bite:

- Case folding beyond ASCII is version-dependent. Gate: FTS is used only for
  queries that are pure printable ASCII; non-ASCII queries take the exact scan
  path (Python ``.lower()`` over the shadow rows — still no vault file I/O).
- The tokenizer needs >= 3 characters per query term. Gate: queries shorter
  than 3 characters take the exact scan path too (never silently return []).

Every FTS hit is additionally re-verified with a Python substring check against
the shadow row, which kills any over-match a folding difference could produce.
An FTS *under*-match cannot happen on the gated path (ASCII folding never
inserts or removes characters, so ASCII substrings survive any non-ASCII
folding of the content), and a parse failure on the MATCH expression falls back
to the scan path for that query. Result: ``search()`` returns exactly the set a
substring scan of the shadow would return, deterministically sorted.

Lifecycle / failure model
-------------------------
The default DB lives under ``HERMES_HOME/memory/vault_fts/<sha1(abs vault)>.db``
(never hardcoded ``~/.hermes``; ``get_hermes_home`` imported lazily to stay
import-cycle free). Construction creates the shadow table and, when the build's
SQLite has FTS5 + trigram, the virtual table and its triggers. A build without
trigram keeps working in shadow-scan mode (exact, just not index-speed). Only
when the DB itself cannot be opened/created does the constructor raise — the
adapters catch that and fall back to their original rglob+substring matcher.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("hermes.platform.memory.vault_fts")

# Shadow table + FTS5 external-content index over it, following the repo's
# messages_fts pattern (external content + rowid-synced triggers). The shadow
# table is the canonical copy the exact scan path reads; notes_fts only ever
# answers "which rowids contain this substring" on the gated fast path.
_SHADOW_DDL = """
CREATE TABLE IF NOT EXISTS notes (
    path TEXT PRIMARY KEY,
    mtime INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT ''
);
"""

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    path UNINDEXED,
    title,
    content,
    content='notes',
    content_rowid='rowid',
    tokenize='trigram'
);
"""

_FTS_TRIGGERS = ("notes_fts_insert", "notes_fts_delete", "notes_fts_update")

_FTS_TRIGGER_DDL = """
CREATE TRIGGER IF NOT EXISTS notes_fts_insert AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, path, title, content)
    VALUES (new.rowid, new.path, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_delete AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, path, title, content)
    VALUES ('delete', old.rowid, old.path, old.title, old.content);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_update
AFTER UPDATE OF mtime, title, content ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, path, title, content)
    VALUES ('delete', old.rowid, old.path, old.title, old.content);
    INSERT INTO notes_fts(rowid, path, title, content)
    VALUES (new.rowid, new.path, new.title, new.content);
END;
"""


def index_dir(vault_path: str | Path) -> Path:
    """Default storage path of a vault's index DB.

    One DB per vault, keyed by the SHA-1 of the vault's absolute path so two
    distinct vaults never share an index. Lives under
    ``HERMES_HOME/memory/vault_fts/``; ``get_hermes_home`` is imported lazily
    (stdlib-only module, but this keeps vault_fts importable without dragging
    the home resolution in at import time and keeps profiles working).
    """
    from hermes_constants import get_hermes_home

    absolute = os.path.abspath(os.fspath(vault_path))
    digest = hashlib.sha1(os.fsencode(absolute)).hexdigest()
    return Path(get_hermes_home()) / "memory" / "vault_fts" / f"{digest}.db"


def parse_obsidian_frontmatter(content: str) -> Tuple[Dict[str, str], str]:
    """Frontmatter ``{key: value}`` map (keys lowercased) + body of a note.

    Shared by the fabric Obsidian adapter (ContextItem construction) and the
    index (title derivation) so a note's *title* — the only part of a document
    that is not literally inside its raw text when it falls back to the file
    stem — is computed by exactly one parser and can never drift apart. Kept
    intentionally identical to the historic adapter behaviour (``---`` block,
    ``split("---", 2)``, first-colon key/value split, stripped values).
    """
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            front: Dict[str, str] = {}
            for line in parts[1].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    front[key.strip().lower()] = value.strip()
            return front, parts[2].strip()
    return {}, content.strip()


def obsidian_note_title(content: str, stem: str) -> str:
    """Title a note would be reported with: frontmatter ``title`` else stem.

    ``front.get("title", stem)`` semantics — an empty ``title:`` value wins
    over the stem (matches the fabric adapter exactly).
    """
    front, _ = parse_obsidian_frontmatter(content)
    return front.get("title", stem)


class VaultFTSIndex:
    """Incrementally-synced SQLite substring index over a vault of ``*.md`` files."""

    def __init__(self, vault_path: str | Path, *, db_path: Optional[str | Path] = None):
        self.vault_path = os.path.abspath(os.fspath(vault_path))
        self.db_path = (
            os.path.abspath(os.fspath(db_path))
            if db_path is not None
            else str(index_dir(self.vault_path))
        )
        self._lock = threading.RLock()
        self._closed = False
        # True only when this SQLite build serves the trigram fast path; the
        # shadow-scan path stays exact either way.
        self._fts_ready = False
        self._init_db()

    # ------------------------------------------------------------------ #
    # schema lifecycle
    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute(_SHADOW_DDL)
            # FTS5+trigram are optional: a shadow-only index is exact (scan
            # path), so a build without the tokenizer degrades instead of
            # failing. Triggers are installed only while the vtable exists —
            # an INSERT trigger referencing a missing notes_fts would break
            # every sync write (same discipline as hermes_state_fts).
            fts_present = bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'notes_fts'"
            ).fetchone())
            live_triggers = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    f"AND name IN ({','.join('?' for _ in _FTS_TRIGGERS)})",
                    _FTS_TRIGGERS,
                ).fetchall()
            }
            # A gap of unknown extent — an index whose shadow rows were never
            # covered by triggers (older shadow-only DB reopened on a build
            # with trigram, triggers dropped mid-run, crash between DROP and
            # CREATE) — cannot be repaired incrementally, and an empty/stale
            # index would silently under-match. Rebuild it from the shadow:
            # drop + recreate + backfill (never reinstall triggers over a gap).
            gap = (not fts_present) or (live_triggers != set(_FTS_TRIGGERS))
            try:
                if gap:
                    conn.execute("DROP TABLE IF EXISTS notes_fts")
                conn.execute(_FTS_DDL)
                conn.executescript(_FTS_TRIGGER_DDL)
                self._fts_ready = True
                if gap:
                    conn.execute(
                        "INSERT INTO notes_fts(rowid, path, title, content) "
                        "SELECT rowid, path, title, content FROM notes"
                    )
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "no such tokenizer: trigram" not in message and "no such module: fts5" not in message:
                    raise
                for trigger in _FTS_TRIGGERS:
                    conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
                logger.warning(
                    "vault FTS5/trigram unavailable for %s (sqlite %s); "
                    "searching via exact shadow scan: %s",
                    self.db_path, sqlite3.sqlite_version, exc,
                )
            conn.commit()
        finally:
            conn.close()

    def close(self) -> None:
        """Release the index. Connections are per-operation, so this only
        marks the index unusable; sync/search afterwards raise RuntimeError."""
        with self._lock:
            self._closed = True

    # ------------------------------------------------------------------ #
    # incremental sync
    # ------------------------------------------------------------------ #
    def _walk_markdown(self) -> Dict[str, int]:
        """relative POSIX path -> st_mtime_ns for every *.md file in the vault.

        Metadata-only walk: this is the whole per-query cost for an unchanged
        vault; file *contents* are read only for the notes whose mtime moved.
        """
        root = Path(self.vault_path)
        disk: Dict[str, int] = {}
        for entry in root.rglob("*.md"):
            if not entry.is_file():
                continue
            try:
                disk[entry.relative_to(root).as_posix()] = entry.stat().st_mtime_ns
            except OSError:
                # Vanished mid-walk (or unreadable): leave it to the deletion pass.
                continue
        return disk

    def _read_rel(self, relative_path: str) -> Optional[str]:
        """Raw note text, decoded with replacement like the B3 adapter does."""
        try:
            with open(os.path.join(self.vault_path, relative_path), "r",
                      encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return None

    def sync(self) -> None:
        """Refresh the shadow+index with only what changed since last sync.

        Idempotent. Walks the vault for ``(path, mtime_ns)``, diffs against the
        shadow table and inserts/updates/deletes exactly the moved rows inside
        one write transaction, so a vault with thousands of notes costs a
        metadata walk + reads of the few changed files.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("VaultFTSIndex is closed")
            disk = self._walk_markdown()
            conn = self._connect()
            try:
                # Serialize writers (WAL allows many readers): the diff below
                # must see a stable shadow snapshot to avoid lost updates.
                conn.execute("BEGIN IMMEDIATE")
                try:
                    shadow = {
                        row[0]: row[1]
                        for row in conn.execute("SELECT path, mtime FROM notes")
                    }
                    gone = sorted(p for p in shadow if p not in disk)
                    changed = sorted(
                        p for p, mtime in disk.items() if shadow.get(p) != mtime
                    )
                    if not gone and not changed:
                        conn.execute("ROLLBACK")
                        return
                    for relative in gone:
                        conn.execute("DELETE FROM notes WHERE path = ?", (relative,))
                    for relative in changed:
                        content = self._read_rel(relative)
                        if content is None:
                            conn.execute("DELETE FROM notes WHERE path = ?", (relative,))
                            continue
                        stem = Path(relative).stem
                        title = obsidian_note_title(content, stem)
                        if relative in shadow:
                            conn.execute(
                                "UPDATE notes SET mtime = ?, title = ?, content = ? "
                                "WHERE path = ?",
                                (disk[relative], title, content, relative),
                            )
                        else:
                            conn.execute(
                                "INSERT INTO notes(path, mtime, title, content) "
                                "VALUES (?, ?, ?, ?)",
                                (relative, disk[relative], title, content),
                            )
                    conn.execute("COMMIT")
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
            finally:
                conn.close()

    # ------------------------------------------------------------------ #
    # search
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        *,
        match_title: bool = True,
        match_content: bool = True,
    ) -> List[str]:
        """Relative POSIX paths (sorted) whose note matches ``query`` as a
        case-insensitive substring.

        ``match_title``/``match_content`` pick which shadow fields participate:
        the fabric adapter matches title-or-content (a stem fallback title is
        real match surface there), the B3 adapter matches content only.
        Refreshes the index first, so results always reflect the vault on disk.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("VaultFTSIndex is closed")
            self.sync()
            return self._search_synced(query, match_title=match_title,
                                       match_content=match_content)

    def _search_synced(self, query: str, *, match_title: bool,
                       match_content: bool) -> List[str]:
        columns: List[str] = []
        if match_title:
            columns.append("title")
        if match_content:
            columns.append("content")
        if not columns:
            return []
        lowered = query.lower()

        conn = self._connect()
        try:
            if not query:
                return [
                    row[0]
                    for row in conn.execute("SELECT path FROM notes ORDER BY path")
                ]

            # FTS fast path is sound only for pure printable ASCII >= 3 chars
            # (see module docstring); every hit is re-verified below so a
            # folding difference can never surface a wrong result.
            if self._fts_ready and _fts_fast_path_applies(query):
                hits = self._fts_candidates(conn, query, columns)
                if hits is not None:
                    return self._verify(conn, hits, lowered, columns)
            return self._scan(conn, lowered, columns)
        finally:
            conn.close()

    def _fts_candidates(
        self, conn: sqlite3.Connection, query: str,
        columns: Sequence[str],
    ) -> Optional[List[str]]:
        """Rowids' paths matched by the trigram index, or None on any query-
        parse failure (caller then uses the exact scan path)."""
        phrase = _fts_escape(query)
        expression = " OR ".join(f"{column} : {phrase}" for column in columns)
        try:
            rows = conn.execute(
                "SELECT n.path FROM notes_fts "
                "JOIN notes n ON n.rowid = notes_fts.rowid "
                "WHERE notes_fts MATCH ?",
                (expression,),
            ).fetchall()
            return [row[0] for row in rows]
        except sqlite3.Error:
            logger.info("vault FTS query fell back to exact scan: %r", query, exc_info=True)
            return None

    def _verify(
        self, conn: sqlite3.Connection, paths: List[str],
        lowered: str, columns: Sequence[str],
    ) -> List[str]:
        """Drop FTS over-matches (folding differences) with a real substring
        check over the shadow rows; the FTS path can only over-match, never
        under-match, so this is the last correctness barrier."""
        if not paths:
            return []
        placeholders = ",".join("?" for _ in paths)
        rows = conn.execute(
            f"SELECT path, title, content FROM notes WHERE path IN ({placeholders})",
            paths,
        ).fetchall()
        matched = []
        for path, title, content in rows:
            if _substring_hit(lowered, columns, title, content):
                matched.append(path)
        return sorted(matched)

    def _scan(self, conn: sqlite3.Connection, lowered: str,
              columns: Sequence[str]) -> List[str]:
        """Exact fallback: Python substring over every shadow row. Used for
        short / non-ASCII queries and shadow-only SQLite builds."""
        rows = conn.execute("SELECT path, title, content FROM notes").fetchall()
        return sorted(
            path
            for path, title, content in rows
            if _substring_hit(lowered, columns, title, content)
        )


def _fts_fast_path_applies(query: str) -> bool:
    """Trigram fast path conditions: >= 3 chars, pure printable ASCII.

    Non-ASCII folding differs across SQLite builds and trigram needs >= 3 chars
    per term; outside this gate the exact scan path preserves the substring
    contract without relying on either.
    """
    return len(query) >= 3 and all(0x20 <= ord(ch) <= 0x7E for ch in query)


def _fts_escape(query: str) -> str:
    """Quote ``query`` as one FTS5 phrase string, doubling embedded quotes."""
    return '"' + query.replace('"', '""') + '"'


def _substring_hit(lowered: str, columns: Sequence[str], title: str,
                   content: str) -> bool:
    return any(
        lowered in (title.lower() if column == "title" else content.lower())
        for column in columns
    )
