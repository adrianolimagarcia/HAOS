"""FederatedMemoryCoordinator — Orquestrador Unificado do Memory Fabric.

Unifica:
- Hermes Memory (Core / Native Upstream Provider)
- Obsidian Vault (Canonical Human-Auditable Truth)
- GraphRAG (Derived Conceptual & Relational Graph Projection)
- DecisionStore (Architectural Decision Records com rastreamento temporal)

Sob um único coordenador autoritativo, suportando:
- Escopos estritos: `private`, `team`, `project`, `global`.
- Pipeline de background writing & consolidação assíncrona/síncrona via ingest_candidate_fact().
- Deduplicação léxica & semântica com detecção de conflitos e tracking de supersedes / superseded_by.
- Sincronização automática multi-store: escrita de markdown canônico no Obsidian Vault,
  atualização incremental de relações no GraphRAG e notificação ao provedor upstream de memória.

Restrições estritas:
- Stdlib-only imports em hermes/platform/.
- PEP-420 namespace compliance (NUNCA criar __init__.py em hermes/platform/).
"""

from __future__ import annotations

import difflib
import logging
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Set

from hermes.platform.context.memory.candidate import (
    DestinationType,
    MemoryCandidate,
    ScopeType,
    StatusType,
)
from hermes.platform.context.memory.consolidation import MemoryConsolidator
from hermes.platform.context.memory.decisions import ArchitectureDecision, DecisionStore
from hermes.platform.context.memory.events import (
    KnowledgeEvent,
    KnowledgeEventBus,
    KnowledgeEventType,
)
from hermes.platform.context.memory.graphrag import GraphRAGAdapter
from hermes.platform.context.memory.graphrag_store import GraphRAGStore
from hermes.platform.context.memory.incremental_graphrag import IncrementalGraphRAGUpdater
from hermes.platform.context.memory.obsidian import ObsidianAdapter
from hermes.platform.context.memory.provider import HermesFabricMemoryProvider
from hermes.platform.context.memory.router import MemoryRouter
from hermes.platform.context.memory.schemas import KnowledgeItem
from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore, MemoryRecord
from hermes.platform.context.memory.projection_runner import ProjectionRunner

logger = logging.getLogger(__name__)

VALID_SCOPES: Set[str] = {"private", "team", "project", "global"}


@dataclass
class FederatedFactRecord:
    """Registro consolidado de fato ou decisão no coordenador federado."""

    id: str
    fact: str
    scope: ScopeType
    provenance: List[str] = field(default_factory=list)
    confidence: float = 1.0
    proposed_destination: DestinationType = "obsidian"
    status: StatusType = "consolidated"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    supersedes: List[str] = field(default_factory=list)
    superseded_by: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "fact": self.fact,
            "scope": self.scope,
            "provenance": list(self.provenance),
            "confidence": self.confidence,
            "proposed_destination": self.proposed_destination,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "supersedes": list(self.supersedes),
            "superseded_by": self.superseded_by,
            "metadata": dict(self.metadata),
        }


class FederatedMemoryCoordinator:
    """Coordenador autoritativo unificado de memória para o ecossistema HAOS / Hermes."""

    def __init__(
        self,
        vault_path: Optional[str | Path] = None,
        obsidian_adapter: Optional[ObsidianAdapter] = None,
        graphrag_adapter: Optional[GraphRAGAdapter] = None,
        memory_provider: Optional[HermesFabricMemoryProvider] = None,
        decision_store: Optional[DecisionStore] = None,
        event_bus: Optional[KnowledgeEventBus] = None,
        graphrag_store: Optional[GraphRAGStore] = None,
        auto_start_worker: bool = False,
    ) -> None:
        if obsidian_adapter is not None:
            self.obsidian = obsidian_adapter
        else:
            self.obsidian = ObsidianAdapter(vault_path=vault_path)
        self.graphrag = graphrag_adapter or GraphRAGAdapter()
        self.decisions = decision_store or DecisionStore()
        self.event_bus = event_bus or KnowledgeEventBus()
        # Canonical state is SQLite; all other stores below are projections.
        if vault_path is None:
            from hermes_constants import get_hermes_home
            ledger_root = Path(get_hermes_home())
        else:
            ledger_root = Path(vault_path).parent
        self.canonical_store = CanonicalMemoryStore(ledger_root / "memory" / "fabric.db")

        # Store persistente do grafo (GOV-008): quando o fabric sincroniza
        # conhecimento, cada KnowledgeEvent publicado é espelhado no store
        # canônico ($HERMES_HOME/memory/graphrag.db) e sobrevive a reinícios,
        # legível pela ferramenta graphrag_query (stack B). Criação é LAZY (na
        # primeira sync): instanciar o coordinator para leitura/status não
        # deve criar arquivos. Store injetado pertence ao chamador.
        self.graphrag_store = graphrag_store
        self._owns_graphrag_store = graphrag_store is None

        # Incremental GraphRAG updater ouvindo o event bus
        self.graphrag_updater = IncrementalGraphRAGUpdater(
            graphrag_adapter=self.graphrag,
            event_bus=self.event_bus,
            auto_subscribe=True,
            store=self.graphrag_store,
        )

        # Provedor upstream
        self.memory_provider = memory_provider or HermesFabricMemoryProvider(
            obsidian_adapter=self.obsidian,
            graphrag_adapter=self.graphrag,
            decision_store=self.decisions,
            canonical_store=self.canonical_store,
            write_handler=self.ingest_candidate_fact,
            recovery_handler=self.recover_projections,
        )
        self.projection_runner = ProjectionRunner(
            self.canonical_store,
            {
                "obsidian": self._project_obsidian,
                "decisions": self._project_decisions,
                "graphrag": self._project_graphrag,
            },
        )

        self.router = MemoryRouter()
        self.consolidator = MemoryConsolidator()

        # CanonicalMemoryStore is the only durable and queryable fact state.

        # Fila e thread de background worker para ingestão assíncrona
        self._ingest_queue: queue.Queue[Optional[MemoryCandidate]] = queue.Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_worker = threading.Event()
        self._lock = threading.RLock()

        if auto_start_worker:
            self.start_background_worker()

    def start_background_worker(self) -> None:
        """Inicia a thread de consolidação e escrita em segundo plano."""
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._stop_worker.clear()
            self._worker_thread = threading.Thread(
                target=self._background_worker_loop,
                daemon=True,
                name="FederatedMemoryWorker",
            )
            self._worker_thread.start()

    def stop_background_worker(self, timeout: float = 2.0) -> None:
        """Para graciosamente a thread de consolidação em segundo plano."""
        with self._lock:
            if self._worker_thread is None:
                return
            self._stop_worker.set()
            self._ingest_queue.put(None)
            self._worker_thread.join(timeout=timeout)
            self._worker_thread = None

    def _background_worker_loop(self) -> None:
        """Loop contínuo de processamento da fila de ingestão."""
        while not self._stop_worker.is_set():
            try:
                candidate = self._ingest_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if candidate is None:
                self._ingest_queue.task_done()
                break

            try:
                self._process_candidate(candidate)
            except Exception as e:
                logger.error("Erro ao processar candidato em background: %s", e)
            finally:
                self._ingest_queue.task_done()

    def ingest_candidate_fact(
        self,
        fact: str,
        scope: ScopeType = "project",
        provenance: Optional[List[str] | str] = None,
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
        sync: bool = True,
    ) -> MemoryCandidate:
        """Ingere um fato candidato, validando escopo, executando deduplicação e sincronização.

        Se sync=True, executa imediatamente a consolidação e sync multi-store no chamador.
        Se sync=False, enfileira para a thread em background.
        """
        if scope not in VALID_SCOPES:
            raise ValueError(f"Escopo inválido: '{scope}'. Deve ser um dos: {sorted(VALID_SCOPES)}")

        prov_list: List[str] = []
        if isinstance(provenance, str):
            prov_list = [provenance]
        elif isinstance(provenance, list):
            prov_list = [p for p in provenance if isinstance(p, str)]

        primary_source = prov_list[0] if prov_list else ""
        candidate = self.router.route_fact(
            fact=fact,
            source_uri=primary_source,
            confidence=confidence,
            scope=scope,
        )
        if prov_list:
            candidate.provenance = prov_list

        if metadata:
            if not hasattr(candidate, "metadata"):
                candidate.metadata = {}  # type: ignore
            candidate.metadata.update(metadata)  # type: ignore
            if "id" in metadata and metadata["id"]:
                candidate.id = metadata["id"]

        if sync:
            self._process_candidate(candidate)
        else:
            self._ingest_queue.put(candidate)

        return candidate

    def _process_candidate(self, candidate: MemoryCandidate) -> bool:
        """Pipeline central de processamento:
        1. Deduplicação léxica e semântica com fatos existentes no mesmo escopo.
        2. Resolução de conflito e supersessão temporal.
        3. Criação ou atualização do FederatedFactRecord.
        4. Sincronização multi-store (Obsidian, GraphRAG, Upstream Hermes Memory).
        """
        with self._lock:
            scope = candidate.scope
            # Dedupe and supersession must survive restart; read the journal.
            existing_records = self.list_facts(scope=scope, include_superseded=False)

            # 1. Deduplicação léxica / semântica
            exact_or_dup_id = self._find_duplicate_fact(candidate, existing_records)
            if exact_or_dup_id is not None:
                # Canonical dedupe is append-only; do not mutate an in-memory
                # shadow record and accidentally diverge provenance/state.
                candidate.status = "consolidated"
                candidate.id = exact_or_dup_id
                return True

            # 2. Detecção de conflito e supersessão temporal
            superseded_ids = self._detect_supersessions(candidate, existing_records)

            record_id = candidate.id or f"fact-{uuid.uuid4().hex[:8]}"
            candidate.id = record_id

            fact_record = FederatedFactRecord(
                id=record_id,
                fact=candidate.fact,
                scope=candidate.scope,
                provenance=list(candidate.provenance),
                confidence=candidate.confidence,
                proposed_destination=candidate.proposed_destination,
                status="consolidated",
                created_at=candidate.created_at or time.time(),
                updated_at=time.time(),
                supersedes=list(superseded_ids),
                metadata=getattr(candidate, "metadata", {}) if hasattr(candidate, "metadata") else {},
            )

            # Commit canonical state and durable outbox before any projection.
            # record_id is the idempotency key, so retries cannot create a second fact.
            stored_record = self.canonical_store.append(
                content=fact_record.fact,
                scope=fact_record.scope,
                kind="decision" if fact_record.proposed_destination == "obsidian" else "fact",
                logical_id=fact_record.metadata.get("logical_id") or fact_record.id,
                provenance=tuple({"uri": uri} for uri in fact_record.provenance),
                confidence=fact_record.confidence,
                metadata=fact_record.metadata,
                supersedes=fact_record.supersedes,
                idempotency_key=fact_record.id,
                valid_from=fact_record.created_at,
            )
            # A restart has no in-memory dedupe index. The journal therefore
            # decides whether this command was a duplicate; never project a
            # synthetic ID that did not become a canonical record.
            if stored_record.record_id != fact_record.id:
                candidate.id = stored_record.record_id
                candidate.status = "consolidated"
                return True

            candidate.status = "consolidated"

            # 3. Sincronização Multi-Store
            self._sync_stores(fact_record, superseded_ids)
            return True

    def _find_duplicate_fact(
        self, candidate: MemoryCandidate, records: List[FederatedFactRecord]
    ) -> Optional[str]:
        """Identifica duplicata exata ou quase idêntica (similaridade >= 0.88)."""
        cand_norm = self._normalize(candidate.fact)
        for rec in records:
            rec_norm = self._normalize(rec.fact)
            if cand_norm == rec_norm:
                return rec.id
            similarity = difflib.SequenceMatcher(None, cand_norm, rec_norm).ratio()
            if similarity >= 0.88:
                return rec.id
        return None

    def _detect_supersessions(
        self, candidate: MemoryCandidate, records: List[FederatedFactRecord]
    ) -> List[str]:
        """Detecta se o candidato supersede fatos anteriores (decisões opostas ou regra explícita)."""
        superseded: List[str] = []
        cand_meta = getattr(candidate, "metadata", {}) if hasattr(candidate, "metadata") else {}
        explicit_supersedes = cand_meta.get("supersedes")
        if isinstance(explicit_supersedes, list):
            superseded.extend([s for s in explicit_supersedes if isinstance(s, str)])
        elif isinstance(explicit_supersedes, str):
            superseded.append(explicit_supersedes)

        # Heurística de conflito semântico (negação ou mudança de regra sobre mesmo tópico)
        cand_norm = self._normalize(candidate.fact)
        cand_words = set(cand_norm.split())

        for rec in records:
            if rec.id in superseded:
                continue

            rec_norm = self._normalize(rec.fact)
            rec_words = set(rec_norm.split())

            overlap = cand_words & rec_words
            meaningful_overlap = {w for w in overlap if len(w) > 3}
            if len(meaningful_overlap) >= 2:
                is_contradiction = self._is_contradiction_or_update(cand_norm, rec_norm)
                if is_contradiction:
                    superseded.append(rec.id)

        return superseded

    def _is_contradiction_or_update(self, text_a: str, text_b: str) -> bool:
        """Verifica se text_a atualiza/contradiz text_b."""
        negations = {"not", "never", "no", "cannot", "nao", "nunca", "deprecated", "prohibited", "disabled", "enabled"}
        words_a = set(text_a.split())
        words_b = set(text_b.split())
        has_neg_a = bool(words_a & negations)
        has_neg_b = bool(words_b & negations)
        if has_neg_a != has_neg_b:
            return True

        update_patterns = [r"instead of", r"supersedes", r"substitui", r"switched to", r"migrated to", r"changed to"]
        if any(re.search(p, text_a) for p in update_patterns):
            return True

        return False

    def _normalize(self, text: str) -> str:
        """Normalização de texto para deduplicação robusta."""
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _ensure_graphrag_store(self) -> GraphRAGStore:
        """Garante o store canônico antes da primeira sync de conhecimento.

        Cria no default canônico e liga no updater ANTES do publish do
        KnowledgeEvent, para o write-through capturar o evento corrente.
        """
        if self.graphrag_store is None:
            self.graphrag_store = GraphRAGStore()
            self.graphrag_updater.store = self.graphrag_store
        return self.graphrag_store

    @staticmethod
    def _from_stored(stored: MemoryRecord) -> FederatedFactRecord:
        return FederatedFactRecord(
            id=stored.record_id, fact=stored.content, scope=stored.scope,
            provenance=[str(p.get("uri", "")) for p in stored.provenance if p.get("uri")],
            confidence=stored.confidence,
            proposed_destination="obsidian" if stored.kind == "decision" else "memory",
            created_at=stored.valid_from, updated_at=stored.valid_from,
            supersedes=list(stored.supersedes), metadata=stored.metadata,
        )

    @staticmethod
    def _is_decision(record: FederatedFactRecord) -> bool:
        return (record.proposed_destination == "obsidian" or "ADR" in record.fact
            or "decision" in record.fact.lower()
            or "decision" in str(record.metadata.get("type", "")).lower())

    @staticmethod
    def _projection_meta(record: FederatedFactRecord) -> Dict[str, Any]:
        return {**record.metadata, "id": record.id, "scope": record.scope,
            "confidence": record.confidence, "provenance": record.provenance,
            "supersedes": record.supersedes, "created_at": record.created_at,
            "updated_at": record.updated_at, "fabric_committed": True}

    def _project_obsidian(self, stored: MemoryRecord) -> None:
        record = self._from_stored(stored)
        is_decision = self._is_decision(record)
        title = record.metadata.get("title") or "Memory Note - %s" % record.id
        relative_path = ("20-Architecture/%s.md" % record.id if is_decision
            else "10-Memory/%s/%s.md" % (record.scope, record.id))
        lines = ["# %s\n" % title, "**Scope:** `%s` | **Confidence:** `%.2f`\n" % (record.scope, record.confidence), "**Status:** `consolidated`\n"]
        if record.supersedes:
            lines.append("**Supersedes:** %s\n" % ", ".join(record.supersedes))
        lines.extend(["\n## Content\n", record.fact, "\n"])
        self.obsidian.write_note(relative_path, title, "\n".join(lines),
            doc_type="architecture_decision" if is_decision else "memory_fact",
            metadata=self._projection_meta(record))

    def _project_decisions(self, stored: MemoryRecord) -> None:
        record = self._from_stored(stored)
        if not self._is_decision(record):
            return
        title = record.metadata.get("title") or "Decision - %s" % record.id
        self.decisions.record_decision(record.id, title, record.fact, supersedes=record.supersedes)

    def _project_graphrag(self, stored: MemoryRecord) -> None:
        record = self._from_stored(stored)
        self._ensure_graphrag_store()
        title = record.metadata.get("title") or "Memory Note - %s" % record.id
        event = KnowledgeEvent.create(
            event_type=KnowledgeEventType.DECISION_RECORDED if self._is_decision(record) else KnowledgeEventType.NOTE_CREATED,
            event_id="memory.changed:" + record.id, uri="memory://" + record.id,
            title=title, content=record.fact, metadata=self._projection_meta(record),
        )
        self.event_bus.publish(event, enqueue=False)

    def _sync_stores(self, record: FederatedFactRecord, superseded_ids: List[str]) -> None:
        """Drain durable projection jobs; projections never write canonical state."""
        self.projection_runner.drain()

    def recover_projections(self, worker_id: str = "fabric-recovery") -> int:
        self.projection_runner.worker_id = worker_id
        return self.projection_runner.drain()

    def get_fact(self, fact_id: str) -> Optional[FederatedFactRecord]:
        """Read through the canonical journal; _facts is only a transient cache."""
        stored = self.canonical_store.get(fact_id)
        return self._from_stored(stored) if stored is not None else None

    def list_facts(self, scope: Optional[ScopeType] = None, include_superseded: bool = False) -> List[FederatedFactRecord]:
        if scope and scope not in VALID_SCOPES:
            raise ValueError("Escopo inválido: %r" % scope)
        scopes = (scope,) if scope else tuple(sorted(VALID_SCOPES))
        return [self._from_stored(record) for record in self.canonical_store.list_records(scopes, include_superseded)]

    def query(self, text: str, scope: Optional[ScopeType] = None, include_superseded: bool = False) -> List[FederatedFactRecord]:
        if scope and scope not in VALID_SCOPES:
            raise ValueError("Escopo inválido: %r" % scope)
        scopes = (scope,) if scope else tuple(sorted(VALID_SCOPES))
        if not text.strip():
            return self.list_facts(scope=scope, include_superseded=include_superseded)
        if not include_superseded:
            return [self._from_stored(record) for record in self.canonical_store.search_fts(text, scopes)]
        needle = self._normalize(text)
        return [record for record in self.list_facts(scope=scope, include_superseded=True) if needle in self._normalize(record.fact)]

    def close(self) -> None:
        """Encerra threads em background e fecha recursos."""
        self.stop_background_worker()
        self.memory_provider.shutdown()
        if self._owns_graphrag_store and self.graphrag_store is not None:
            self.graphrag_store.close()
        self.canonical_store.close()
