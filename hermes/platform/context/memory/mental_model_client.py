"""Profile-bound, transactional mental-model projection in the RAGGraph database.

The native C ABI reads ``<home>/memory/raggraph.db``, not ``state.db``.
Python owns writes because the native read/modify/write delta entry point has no
transaction spanning the read and write and cannot report idempotent no-ops.
"""
from __future__ import annotations

import ctypes
import json
import logging
import sqlite3
import time
from pathlib import Path

from .schemas import MentalModelASTSchema, MentalModelSectionSchema, MentalModelBlockSchema

logger = logging.getLogger(__name__)
_NATIVE_PATH = Path('/usr/local/lib/haos/libhaos_edge.so')


class MentalModelClient:
    def __init__(self, home: Path, native_path: Path | None = None):
        self.home = Path(home).resolve()
        self.db_path = self.home / 'memory' / 'raggraph.db'
        self.native = None
        # The current Rust path resolver searches other homes when state.db is
        # absent. Never invoke its ABI unless this exact profile wins that search.
        if (self.home / 'state.db').is_file():
            try:
                lib = ctypes.CDLL(str(native_path or _NATIVE_PATH))
                lib.mental_model_get_compiled_buffered.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int]
                lib.mental_model_get_compiled_buffered.restype = ctypes.c_int
                lib.tempr_search_buffered.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_double, ctypes.c_double, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
                lib.tempr_search_buffered.restype = ctypes.c_int
                self.native = lib
            except (OSError, AttributeError) as exc:
                logger.info('Mental-model native library unavailable; SQLite projection remains available: %s', exc)

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.execute('''CREATE TABLE IF NOT EXISTS haos_mental_models (
                model_id TEXT PRIMARY KEY, title TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                ast_json TEXT NOT NULL, compiled_markdown TEXT NOT NULL, token_count INTEGER NOT NULL,
                last_refreshed_at INTEGER NOT NULL, last_memory_write_at INTEGER NOT NULL)''')
            return conn
        except BaseException:
            conn.close()
            raise

    def apply_delta(self, model_id: str, delta: dict) -> bool:
        """Apply one delta, returning True only when committed content changed."""
        return self.apply_deltas(model_id, [delta])

    def apply_deltas(self, model_id: str, deltas: list[dict]) -> bool:
        """Atomically apply a batch; BEGIN IMMEDIATE serializes concurrent writers."""
        if not model_id or not deltas or not all(isinstance(d, dict) for d in deltas):
            raise ValueError('model_id and nonempty deltas required')
        conn = self._connect()
        try:
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT ast_json FROM haos_mental_models WHERE model_id=?', (model_id,)).fetchone()
                doc = MentalModelASTSchema.from_dict(json.loads(row[0])) if row else MentalModelASTSchema(model_id, model_id)
                changed = False
                for delta in deltas:
                    section_id = delta['section_id']
                    sec = next((s for s in doc.sections if s.section_id == section_id), None)
                    op = delta['type']
                    if op == 'add_section':
                        title, order = delta['title'], int(delta.get('order', 0))
                        if sec is None:
                            doc.sections.append(MentalModelSectionSchema(section_id, title, order))
                            changed = True
                        elif (sec.title, sec.order) != (title, order):
                            sec.title, sec.order = title, order
                            changed = True
                    elif op == 'append_block':
                        if sec is None:
                            raise ValueError(f'Section not found: {section_id}')
                        incoming = MentalModelBlockSchema.from_dict(delta['block'])
                        block = next((b for b in sec.blocks if b.block_id == incoming.block_id), None)
                        if block is None:
                            sec.blocks.append(incoming)
                            changed = True
                        elif block.to_dict() != incoming.to_dict():
                            block.content, block.proof_count, block.source_node_ids = incoming.content, incoming.proof_count, incoming.source_node_ids
                            changed = True
                    elif op in ('replace_block', 'remove_block'):
                        if sec is None:
                            raise ValueError(f'Section not found: {section_id}')
                        block = next((b for b in sec.blocks if b.block_id == delta['block_id']), None)
                        if block is None:
                            raise ValueError(f'Block not found: {delta["block_id"]}')
                        if op == 'remove_block':
                            sec.blocks.remove(block)
                            changed = True
                        else:
                            content = delta['new_content']
                            increment = int(delta.get('proof_increment', 0))
                            if increment < 0:
                                raise ValueError('proof_increment must be nonnegative')
                            if block.content != content or increment:
                                block.content = content
                                block.proof_count += increment
                                changed = True
                    else:
                        raise ValueError(f'Unsupported delta: {op}')
                if changed:
                    doc.version += 1
                    markdown = doc.compile_to_markdown()
                    now = int(time.time())
                    conn.execute('''INSERT INTO haos_mental_models VALUES (?,?,?,?,?,?,?,?)
                        ON CONFLICT(model_id) DO UPDATE SET title=excluded.title, version=excluded.version,
                        ast_json=excluded.ast_json, compiled_markdown=excluded.compiled_markdown,
                        token_count=excluded.token_count, last_refreshed_at=excluded.last_refreshed_at,
                        last_memory_write_at=excluded.last_memory_write_at''',
                        (model_id, doc.title, doc.version, json.dumps(doc.to_dict(), ensure_ascii=False),
                         markdown, len(markdown.encode()) // 4, now, now))
                return changed
        finally:
            conn.close()

    def get_compiled_markdown(self, model_id: str) -> str | None:
        if not self.db_path.is_file():
            return None
        # Read the same database that apply_deltas writes. This also avoids
        # native buffer truncation and silent cross-profile fallback.
        conn = sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True)
        try:
            row = conn.execute('SELECT compiled_markdown FROM haos_mental_models WHERE model_id=?', (model_id,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def tempr_search(self, query: str, time_start: float = 0, time_end: float = 0, limit: int = 10) -> list[dict]:
        """Native hybrid TEMPR only; a substring scan is not an equivalent fallback."""
        if limit <= 0 or not query.strip():
            return []
        if self.native is None or not self.db_path.is_file() or not (self.home / 'state.db').is_file():
            raise RuntimeError('Native TEMPR is unavailable for this profile')
        buf = ctypes.create_string_buffer(1024 * 1024)
        rc = self.native.tempr_search_buffered(str(self.home).encode(), query.encode(), time_start, time_end, limit, buf, len(buf))
        if rc < 0 or rc >= len(buf):
            raise RuntimeError(f'Native TEMPR failed (C-ABI code {rc})')
        return json.loads(buf.raw[:rc])
