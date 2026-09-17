"""HermesFabricMemoryProvider — Provedor de memória federado para o Hermes Agent.

Do ponto de vista do Hermes upstream:
Existe apenas UM MemoryProvider externo ativo (`memory.provider: hermes-fabric`).

Do ponto de vista interno do HAOS:
Existe uma federação orquestrada de fontes:
- L0: Core Memory nativo (MEMORY.md / USER.md) congelado no system prompt
- L3: ObsidianAdapter (Canônico humano e auditável)
- L4: DecisionStore (ADRs e decisões arquiteturais com controle temporal)
- L5: GraphRAGAdapter (Projeção relacional derivada de comunidades e grafos)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from hermes.platform.context.memory.decisions import DecisionStore
from hermes.platform.context.memory.graphrag import GraphRAGAdapter
from hermes.platform.context.memory.obsidian import ObsidianAdapter

logger = logging.getLogger(__name__)


class HermesFabricMemoryProvider(MemoryProvider):
    """MemoryProvider federado que orquestra Obsidian, GraphRAG e DecisionStore."""

    def __init__(
        self,
        vault_path: Optional[Path | str] = None,
        obsidian_adapter: Optional[ObsidianAdapter] = None,
        graphrag_adapter: Optional[GraphRAGAdapter] = None,
        decision_store: Optional[DecisionStore] = None,
    ):
        if isinstance(vault_path, str):
            self.vault_path = Path(vault_path)
        else:
            # Fork HAOS: resolve o vault pelo home canônico (get_hermes_home),
            # nunca por ~/.hermes nem por caminho relativo ao cwd — ambos
            # produziam split-brain (vault invisível) fora da ISO.
            from hermes_constants import get_hermes_home

            home = Path(get_hermes_home())
            self.vault_path = vault_path or (home / "obsidian_vault")
        self.obsidian = obsidian_adapter or ObsidianAdapter(self.vault_path)
        self.decisions = decision_store or DecisionStore()
        self.graphrag = graphrag_adapter or GraphRAGAdapter()
        self._session_id: str = ""
        from hermes_constants import get_hermes_home

        self._hermes_home: Path = Path(get_hermes_home())
        self._initialized: bool = False
        self._prefetch_cache: Dict[tuple[str, str], str] = {}
        self._recent_recall: Dict[str, str] = {}
        self._turn_seen: set[tuple[str, str]] = set()
        self._write_lock = threading.RLock()
        self._dedupe_path = self._hermes_home / "memory" / "fabric_dedupe.json"
        self._ledger_path = self._hermes_home / "memory" / "fabric_ledger.db"
        self._ledger: Optional[sqlite3.Connection] = None

    def is_available(self) -> bool:
        """Check that the configured vault is usable without performing network I/O."""
        try:
            return self.vault_path.parent.exists()
        except OSError:
            return False

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Inicializa conexões e diretórios de armazenamento."""
        self._session_id = session_id
        if "hermes_home" in kwargs:
            self._hermes_home = Path(kwargs["hermes_home"])
            self._dedupe_path = self._hermes_home / "memory" / "fabric_dedupe.json"
            self._ledger_path = self._hermes_home / "memory" / "fabric_ledger.db"
            if self._ledger is not None:
                self._ledger.close()
                self._ledger = None
            self.vault_path = self._hermes_home / "obsidian_vault"
            self.obsidian.set_vault_path(self.vault_path)
        self._load_dedupe_index()
        self._open_ledger()

        self.vault_path.mkdir(parents=True, exist_ok=True)
        self._initialized = True
        logger.info("HermesFabricMemoryProvider initialized for session %s at %s", session_id, self.vault_path)

    def _load_dedupe_index(self) -> None:
        try:
            data = json.loads(self._dedupe_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._turn_seen = {tuple(x) for x in data if isinstance(x, list) and len(x) == 2}
        except (OSError, ValueError, TypeError):
            self._turn_seen = set()

    def _persist_dedupe_index(self) -> None:
        self._dedupe_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._dedupe_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self._turn_seen)), encoding="utf-8")
        tmp.replace(self._dedupe_path)

    def _open_ledger(self) -> None:
        self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._ledger = sqlite3.connect(self._ledger_path, check_same_thread=False)
        self._ledger.execute("PRAGMA journal_mode=WAL")
        self._ledger.execute("PRAGMA busy_timeout=30000")
        self._ledger.execute(
            "CREATE TABLE IF NOT EXISTS memories (digest TEXT PRIMARY KEY, memory_id TEXT NOT NULL, scope TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        self._ledger.execute(
            """CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, digest TEXT NOT NULL, sink TEXT NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, last_error TEXT, UNIQUE(digest, sink)
            )"""
        )
        self._ledger.execute("CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(status, next_attempt)")
        self._ledger.commit()

    def _claim_memory(self, digest: str, memory_id: str, scope: str, payload: Dict[str, Any]) -> bool:
        if self._ledger is None:
            self._open_ledger()
        assert self._ledger is not None
        try:
            self._ledger.execute("BEGIN IMMEDIATE")
            self._ledger.execute(
                "INSERT INTO memories(digest,memory_id,scope,created_at) VALUES(?,?,?,strftime('%s','now'))",
                (digest, memory_id, scope),
            )
            encoded = json.dumps(payload, sort_keys=True)
            for sink in ("decision", "obsidian", "graphrag"):
                self._ledger.execute(
                    "INSERT INTO outbox(digest,sink,payload) VALUES(?,?,?)",
                    (digest, sink, encoded),
                )
            self._ledger.commit()
            return True
        except sqlite3.IntegrityError:
            self._ledger.rollback()
            return False

    def _replay_outbox(self) -> None:
        if self._ledger is None:
            return
        rows = self._ledger.execute(
            "SELECT id, sink, payload, attempts FROM outbox WHERE status != 'done' AND next_attempt <= strftime('%s','now') ORDER BY id"
        ).fetchall()
        for row in rows:
            self._process_outbox_row(row)

    def _process_outbox_row(self, row: sqlite3.Row | tuple) -> None:
        row_id, sink, raw_payload, attempts = row[:4]
        try:
            payload = json.loads(raw_payload)
            if sink == "decision":
                self.decisions.record_decision(payload["id"], payload["title"], payload["content"], payload.get("supersedes"))
            elif sink == "obsidian":
                path = self.vault_path / payload["path"]
                if not path.exists():
                    self.obsidian.write_note(payload["path"], payload["title"], payload["body"], doc_type="architecture_decision", metadata=payload.get("metadata"))
            elif sink == "graphrag":
                self.graphrag.register_entity(payload["id"], "architecture_decision", payload["content"])
                for old_id in payload.get("supersedes", []):
                    self.graphrag.register_relation(payload["id"], str(old_id), "supersedes", "Explicit decision supersession")
            self._ledger.execute("UPDATE outbox SET status='done', last_error=NULL WHERE id=?", (row_id,))
            self._ledger.commit()
        except Exception as exc:
            delay = min(3600, 2 ** min(int(attempts) + 1, 10))
            self._ledger.execute(
                "UPDATE outbox SET status='pending', attempts=attempts+1, next_attempt=strftime('%s','now')+?, last_error=? WHERE id=?",
                (delay, str(exc)[:500], row_id),
            )
            self._ledger.commit()
            logger.warning("Memory Fabric outbox sink %s failed; retry in %ss", sink, delay, exc_info=True)

    def drain_outbox(self) -> None:
        """Drain pending projections; safe to call after startup or a transient failure."""
        with self._write_lock:
            self._replay_outbox()

    def system_prompt_block(self) -> str:
        """Bloco estático, pequeno, que não muta o prompt durante a conversa."""
        return (
            "## Memory Fabric (HAOS Federation)\n"
            "- Architecture & Project Truth: Obsidian Vault (`obsidian://`)\n"
            "- Decisions & Governance: DecisionStore (ADRs)\n"
            "- Relational & Impact Analysis: GraphRAG\n"
            "Use memory queries or context expand tools for deep knowledge retrieval."
        )

    @property
    def name(self) -> str:
        return "hermes-fabric"

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Esquemas de ferramentas de memória exportados para o agente."""
        return []

    def _retrieve_into_cache(self, query: str, session_id: str) -> None:
        blocks: List[str] = []
        seen: set[str] = set()
        sources = (
            ("obsidian", lambda: self.obsidian.retrieve(query=query), 3),
            ("decisions", lambda: self.decisions.retrieve(query=query), 2),
            ("graphrag", lambda: self.graphrag.retrieve(query=query), 2),
        )
        for source_name, retrieve, limit in sources:
            try:
                for item in retrieve()[:limit]:
                    if item.source_uri in seen:
                        continue
                    seen.add(item.source_uri)
                    blocks.append(f"[{item.source_uri}] {item.get_representation('summary')}")
            except Exception:
                logger.warning("Hermes Fabric %s recall failed", source_name, exc_info=True)
        result = "\n\n".join(blocks)
        key = (session_id, query.strip())
        with self._write_lock:
            self._prefetch_cache[key] = result
            if len(self._prefetch_cache) > 128:
                self._prefetch_cache.pop(next(iter(self._prefetch_cache)))

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return prepared recall only; cold retrieval is never on the turn path."""
        if not self._initialized:
            return ""
        sid = session_id or self._session_id
        normalized = query.strip()
        with self._write_lock:
            cached = self._prefetch_cache.get((sid, normalized))
            if cached is not None:
                return cached
            for text, value in self._recent_recall.items():
                if normalized and normalized.lower() in text.lower():
                    return value
            return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        if not self._initialized:
            return
        from agent.memory_provider import spawn_context_thread
        thread = spawn_context_thread(
            self._retrieve_into_cache,
            name="hermes-fabric-prefetch",
            args=(query, session_id or self._session_id),
        )
        thread.start()

    @staticmethod
    def _stable_memory_id(content: str, metadata: Dict[str, Any]) -> str:
        explicit = metadata.get("id")
        if explicit:
            return str(explicit)
        digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()[:20]
        return f"ADR-{digest}"

    def sync_turn(self, user_message: str, assistant_response: str, **kwargs: Any) -> None:
        """Persist only explicit decisions from completed turns; ordinary chat stays ephemeral."""
        if not self._initialized:
            return
        content = assistant_response.strip()
        if not content or not re.search(r"(?:^|\s)(?:ADR|DECISION)\s*[:#-]", content, re.IGNORECASE):
            return
        metadata = dict(kwargs.get("metadata") or {})
        metadata.setdefault("provenance", [f"session://{kwargs.get('session_id') or self._session_id}"])
        metadata.setdefault("source_turn", user_message[:500])
        self._upsert_memory(content, target="architecture", metadata=metadata)

    def shutdown(self) -> None:
        """Encerra recursos de memória de forma graciosa."""
        self._prefetch_cache.clear()
        if self._ledger is not None:
            self._ledger.close()
            self._ledger = None
        self._initialized = False

    def remember(self, content: str, target: str = "notes", metadata: Optional[Dict[str, Any]] = None) -> None:
        """Grava de modo idempotente uma decisão ou fato explícito."""
        self._upsert_memory(content, target=target, metadata=metadata)

    def _upsert_memory(self, content: str, target: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = dict(metadata or {})
        if not ("ADR" in content or target == "architecture"):
            return
        dec_id = self._stable_memory_id(content, meta)
        digest = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()
        scope = str(meta.get("scope") or "project")
        if scope not in {"private", "team", "project", "global"}:
            raise ValueError(f"Invalid memory scope: {scope}")
        with self._write_lock:
            supersedes = meta.get("supersedes")
            if isinstance(supersedes, str):
                supersedes = [supersedes]
            payload = {
                "id": dec_id, "title": str(meta.get("title") or "Architecture Decision"),
                "content": content, "scope": scope,
                "supersedes": supersedes if isinstance(supersedes, list) else [],
                "path": f"20-Architecture/{dec_id}.md",
                "body": f"**Scope:** `{scope}` | **Confidence:** `{meta.get('confidence', 1.0):.2f}`\n\n{content}",
                "metadata": meta,
            }
            if not self._claim_memory(digest, dec_id, scope, payload):
                return
            self.drain_outbox()
            self._recent_recall[content] = f"[decision://{dec_id}] {content}"
            self._prefetch_cache.clear()

    def on_memory_write(self, action: str, target: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Intercepta comandos de escrita da ferramenta `memory` upstream."""
        if action in {"add", "replace"}:
            self._upsert_memory(content, target=target, metadata=metadata)

