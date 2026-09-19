"""FederatedMemoryCoordinator — Orquestrador Unificado do Memory Fabric.

Unifica:
- Hermes Memory (Core / Native Upstream Provider)
- CanonicalMemoryStore (durable source of truth)
- Obsidian Vault (human-auditable derived projection)
- GraphRAG and DecisionStore (rebuildable derived projections)

Sob um único coordenador autoritativo, suportando:
- Escopos estritos: `private`, `team`, `project`, `global`.
- Pipeline de background writing & consolidação assíncrona/síncrona via ingest_candidate_fact().
- Deduplicação léxica & semântica com detecção de conflitos e tracking de supersedes / superseded_by.
- Projeções duráveis e independentes via transactional outbox.

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
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple, Set

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
from hermes.platform.context.memory.canonical_store import (
    SUPERSESSION_DECLARED,
    SUPERSESSION_NEGATION,
    SUPERSESSION_POLARITY,
    SUPERSESSION_UPDATE_WORDING,
    CanonicalMemoryStore,
    MemoryRecord,
    RestoreOutcome,
    SupersessionEdge,
)
from hermes.platform.context.memory.projection_runner import ProjectionRunner
from hermes.platform.context.memory.access import MemoryAccessContext
from hermes.platform.context.memory.embedding import Embedder, HashingEmbedder
from hermes.platform.context.memory.vector_index import SQLiteVectorIndex
from hermes.platform.context.memory.flags import FlagError, MemoryFeatureFlags
from hermes.platform.context.memory.metrics import MemoryFabricMetrics, Timer
from hermes.platform.context.memory.migration import MemoryMigrator
from hermes.platform.context.memory.shadow import LegacyFederatedRetriever, ShadowRetriever

logger = logging.getLogger(__name__)

# Antonym pairs: a real contradiction is the same statement with flipped polarity.
# Kept as explicit surface forms (no stemming) so the rule is auditable and cannot
# surprise anyone by "understanding" more than it says.
_POLARITY_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("enabled", "disabled"),
    ("enable", "disable"),
    ("allowed", "forbidden"),
    ("allow", "forbid"),
    ("required", "optional"),
    ("mandatory", "optional"),
    ("active", "inactive"),
    ("prohibited", "permitted"),
)

# Markers that flip the predicate of the sentence they appear in.
_NEGATION_MARKERS = frozenset(("not", "never", "no", "cannot", "nao", "nunca", "deprecated", "prohibited"))

# How similar the two statements must be, ignoring the negation marker, for the marker to
# be read as the *only* difference between them. Chosen from a measured gap, not by taste:
# when the marker is the only difference the ratio lands at 0.919-1.000, and when it is one
# of several differences it lands at 0.426-0.744, so 0.85 sits inside the empty band.
# No single threshold separates the two classes in general — a lexically similar pair can
# be unrelated ("the legacy api is deprecated" vs "… returns 404", 0.744) while a real
# contradiction can be lexically distant ("… is deprecated" vs "… is supported", 0.667),
# which is why antonym pairs above carry the polarity cases and this gate only handles
# "the marker is the difference". The bias stays toward missing a change.
_NEGATION_SIMILARITY = 0.85

VALID_SCOPES: Set[str] = {"private", "team", "project", "global"}


@dataclass(frozen=True)
class SupersessionDecision:
    """Um fato anterior que o candidato supersede, com o motivo e a confiança.

    ``reason`` é uma das constantes ``SUPERSESSION_*`` do store canônico; ``score`` é a
    confiança da regra que casou.
    """
    superseded_id: str
    reason: str
    score: float


@dataclass(frozen=True)
class ProjectionDrift:
    """Quanto de uma projeção está no disco, comparado ao journal.

    ``expected`` conta os registros **ativos** que deveriam estar projetados; ``missing``
    são os que não estão. ``orphans`` são artefatos cujo registro não existe no journal em
    nenhum status. Um registro superseded não é órfão: notas antigas e vetores de modelo
    antigo são retidos de propósito, para histórico e rollback.
    """
    projection: str
    expected: int
    present: int
    missing: Tuple[str, ...] = ()
    orphans: Tuple[str, ...] = ()
    detail: str = ""

    @property
    def ok(self) -> bool:
        return not self.missing and not self.orphans


@dataclass
class FederatedFactRecord:
    """Registro consolidado de fato ou decisão no coordenador federado."""

    id: str
    fact: str
    scope: ScopeType
    provenance: List[str] = field(default_factory=list)
    confidence: float = 1.0
    proposed_destination: DestinationType = "obsidian"
    status: str = "consolidated"
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
        embedder: Optional[Embedder] = None,
        vector_index: Optional[SQLiteVectorIndex] = None,
        projection_lease_seconds: float = 60.0,
        auto_start_worker: bool = False,
    ) -> None:
        # Canonical state is SQLite; all other stores below are projections.
        if vault_path is None:
            from hermes_constants import get_hermes_home
            ledger_root = Path(get_hermes_home())
        else:
            ledger_root = Path(vault_path).parent

        if obsidian_adapter is not None:
            self.obsidian = obsidian_adapter
        else:
            self.obsidian = ObsidianAdapter(vault_path=vault_path)
        self.graphrag = graphrag_adapter or GraphRAGAdapter()
        # The decisions projection is durable like the others: it used to be a plain
        # dict, so every restart silently emptied it while `recover_projections()`
        # had nothing to replay (the outbox was already acknowledged).
        self._owns_decision_store = decision_store is None
        self.decisions = decision_store or DecisionStore(ledger_root / "memory" / "decisions.db")
        self.event_bus = event_bus or KnowledgeEventBus()
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

        self.router = MemoryRouter()
        self.consolidator = MemoryConsolidator()
        # Metrics and cutover flags are constructed first: the provider, the
        # projection runner and the vector index all record into them.
        self.metrics = MemoryFabricMetrics()
        # A malformed cutover config must not be the thing that takes the appliance down: the
        # fabric is constructed during plugin registration, so raising here would fail startup
        # for the whole agent. Falling back to the defaults is exactly today's behaviour, and the
        # ERROR line is what makes it diagnosable instead of a rollback that quietly did nothing.
        try:
            self.flags = MemoryFeatureFlags.from_config()
        except FlagError as exc:
            logger.error("memory cutover config rejected, using defaults (all stages on): %s", exc)
            self.flags = MemoryFeatureFlags()

        # Provedor upstream
        self.memory_provider = memory_provider or HermesFabricMemoryProvider(
            obsidian_adapter=self.obsidian,
            graphrag_adapter=self.graphrag,
            decision_store=self.decisions,
            canonical_store=self.canonical_store,
            write_handler=self.ingest_candidate_fact,
            recovery_handler=self.recover_projections,
        )
        # An injected provider must share this coordinator's journal, otherwise
        # prefetch reads a different (or absent) canonical store than the one
        # the coordinator commits to.
        if self.memory_provider.canonical_store is None:
            self.memory_provider.canonical_store = self.canonical_store
        if self.memory_provider._write_handler is None:
            self.memory_provider._write_handler = self.ingest_candidate_fact
        if self.memory_provider._recovery_handler is None:
            self.memory_provider._recovery_handler = self.recover_projections
        self.projection_runner = ProjectionRunner(
            self.canonical_store,
            {
                "obsidian": self._project_obsidian,
                "decisions": self._project_decisions,
                "graphrag": self._project_graphrag,
                "embeddings": self._project_embeddings,
            },
            lease_seconds=projection_lease_seconds,
        )

        # Vector projection: the embedder pins model identity/dimensions, and the
        # index is keyed by that identity so switching models is a reindex, never
        # a silent mix of incomparable vectors.
        self.embedder: Embedder = embedder or HashingEmbedder()
        self.vector_index = vector_index or SQLiteVectorIndex(
            ledger_root / "memory" / "vectors.db",
            self.embedder.model.model_id,
            dimensions=self.embedder.model.dimensions,
            normalize=self.embedder.model.normalize,
            reindex_policy=self.embedder.model.reindex_policy,
        )
        self._owns_vector_index = vector_index is None
        self.memory_provider.vector_search = self._vector_search
        self.memory_provider.metrics = self.metrics
        self.memory_provider.flags = self.flags
        self.memory_provider.legacy_reader = self.legacy_context
        self.projection_runner.metrics = self.metrics

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
        access_context: Optional[MemoryAccessContext] = None,
    ) -> MemoryCandidate:
        """Ingere um fato candidato, validando escopo, executando deduplicação e sincronização.

        Se sync=True, executa imediatamente a consolidação e sync multi-store no chamador.
        Se sync=False, enfileira para a thread em background.
        """
        if scope not in VALID_SCOPES:
            raise ValueError(f"Escopo inválido: '{scope}'. Deve ser um dos: {sorted(VALID_SCOPES)}")
        if access_context is not None:
            # The record must carry the tenancy that authorized it, otherwise a
            # later read cannot re-authorize the fact it just accepted.
            metadata = {**(metadata or {}), **access_context.write_metadata(scope)}
            access_context.require_write(scope, metadata)

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
            if not self.flags.enabled("canonical_writes"):
                return self._legacy_ingest(candidate)
            # Dedupe and supersession must survive restart; read the journal.
            existing_records = self.list_facts(scope=scope, include_superseded=False)

            # 1. Deduplicação léxica / semântica
            exact_or_dup_id = self._find_duplicate_fact(candidate, existing_records)
            if exact_or_dup_id is not None:
                # Recurrence is the reinforcement signal: the fact already
                # exists, so raise its confidence and merge provenance instead
                # of appending a second record. Restart-safe because the target
                # is read from the journal, never from an in-memory index.
                self.canonical_store.reinforce(
                    exact_or_dup_id,
                    confidence=candidate.confidence,
                    provenance=tuple({"uri": uri} for uri in candidate.provenance),
                )
                self.metrics.increment("dedupe_hits")
                candidate.status = "consolidated"
                candidate.id = exact_or_dup_id
                return True

            # 2. Detecção de conflito e supersessão temporal
            supersessions = self._detect_supersessions(candidate, existing_records)
            superseded_ids = [decision.superseded_id for decision in supersessions]

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
                supersession_reasons={
                    decision.superseded_id: (decision.reason, decision.score)
                    for decision in supersessions
                },
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
            self.metrics.increment("dedupe_misses")
            self._sync_stores(fact_record, superseded_ids)
            return True

    def _find_duplicate_fact(
        self, candidate: MemoryCandidate, records: List[FederatedFactRecord]
    ) -> Optional[str]:
        """Identifica duplicata exata ou quase idêntica (similaridade >= 0.88).

        Wording quase igual **não** implica mesma afirmação. Uma polaridade invertida
        ou uma negação é uma mudança, não uma recorrência, e absorvê-la como duplicata
        descartaria a correção e continuaria servindo o valor antigo — medido antes da
        correção: "the cache layer is disabled" seguido de "the cache layer is enabled"
        deixava o journal com UM registro, ainda dizendo ``disabled``. Esses casos são
        encaminhados para a supersessão, que é onde a mudança pertence.
        """
        cand_norm = self._normalize(candidate.fact)
        for rec in records:
            rec_norm = self._normalize(rec.fact)
            if cand_norm == rec_norm:
                return rec.id
            similarity = difflib.SequenceMatcher(None, cand_norm, rec_norm).ratio()
            if similarity >= 0.88:
                if self._is_contradiction_or_update(cand_norm, rec_norm):
                    continue
                return rec.id
        return None

    def _detect_supersessions(
        self, candidate: MemoryCandidate, records: List[FederatedFactRecord]
    ) -> List[SupersessionDecision]:
        """Detecta se o candidato supersede fatos anteriores, e **por quê**.

        O motivo não é decoração: uma supersessão automática é um palpite, e sem
        registrá-lo o fato simplesmente deixa de ser lembrado sem que ninguém consiga
        distinguir "um humano decidiu" de "a heurística achou".
        """
        superseded: List[SupersessionDecision] = []
        cand_meta = getattr(candidate, "metadata", {}) if hasattr(candidate, "metadata") else {}
        explicit_supersedes = cand_meta.get("supersedes")
        if isinstance(explicit_supersedes, list):
            declared = [s for s in explicit_supersedes if isinstance(s, str)]
        elif isinstance(explicit_supersedes, str):
            declared = [explicit_supersedes]
        else:
            declared = []
        superseded.extend(
            SupersessionDecision(target, SUPERSESSION_DECLARED, 1.0) for target in declared
        )

        # Heurística de conflito semântico (negação ou mudança de regra sobre mesmo tópico)
        cand_norm = self._normalize(candidate.fact)
        cand_words = set(cand_norm.split())

        for rec in records:
            if any(decision.superseded_id == rec.id for decision in superseded):
                continue

            rec_norm = self._normalize(rec.fact)
            rec_words = set(rec_norm.split())

            overlap = cand_words & rec_words
            meaningful_overlap = {w for w in overlap if len(w) > 3}
            if len(meaningful_overlap) >= 2:
                matched = self._contradiction_reason(cand_norm, rec_norm)
                if matched is not None:
                    reason, score = matched
                    superseded.append(SupersessionDecision(rec.id, reason, score))

        return superseded

    def _is_contradiction_or_update(self, text_a: str, text_b: str) -> bool:
        """Se ``text_a`` contradiz ou atualiza ``text_b`` (ver ``_contradiction_reason``)."""
        return self._contradiction_reason(text_a, text_b) is not None

    def _contradiction_reason(
        self, text_a: str, text_b: str
    ) -> Optional[Tuple[str, float]]:
        """Motivo e confiança da contradição, ou ``None`` se não houver uma.

        A decisão é deliberadamente assimétrica: **preferimos perder uma supersessão a
        inventar uma**. Uma supersessão perdida deixa um fato antigo ativo — visível,
        consultável e corrigível por um humano. Uma supersessão falsa remove um fato
        legítimo do recall, e é por isso que ela também precisa ser auditável e
        reversível (``supersession_edges`` / ``restore``): o viés só é defensável se o
        erro for recuperável.

        Por isso uma negação solta (``not``, ``nunca``, ``deprecated``…) só conta quando
        removê-la deixa as duas frases praticamente idênticas: é o caso "mesma afirmação
        com a polaridade invertida". Sem essa trava, qualquer frase que mencione "no" ou
        "deprecated" supersederia um fato não relacionado que compartilhasse duas
        palavras — medido antes da correção: "the retry queue is enabled by default"
        era supersedido por "the retry queue stores 100 entries".
        """
        # 1. Mesma afirmação com a polaridade invertida (enabled/disabled, allow/forbid…).
        if self._has_opposite_polarity(text_a, text_b):
            return SUPERSESSION_POLARITY, 1.0

        # 2. Atualização explícita declarada no próprio texto novo.
        update_patterns = [r"instead of", r"supersedes", r"substitui", r"switched to", r"migrated to", r"changed to"]
        if any(re.search(p, text_a) for p in update_patterns):
            return SUPERSESSION_UPDATE_WORDING, 1.0

        # 3. Negação presente em apenas um lado, sendo ela a única diferença. O score é a
        # similaridade medida: é a única regra com limiar ajustável, então é a única em que
        # a confiança é um número e não um casamento exato.
        similarity = self._negation_is_the_difference(text_a, text_b)
        if similarity is None:
            return None
        return SUPERSESSION_NEGATION, similarity

    @staticmethod
    def _has_opposite_polarity(text_a: str, text_b: str) -> bool:
        """True quando os dois textos usam lados opostos de um par antônimo.

        ``enabled`` e ``disabled`` estavam no MESMO conjunto de "negações", então se
        anulavam: o par contraditório mais comum era exatamente o que a heurística não
        enxergava.
        """
        words_a = set(text_a.split())
        words_b = set(text_b.split())
        for positive, negative in _POLARITY_PAIRS:
            if (positive in words_a and negative in words_b) or (negative in words_a and positive in words_b):
                return True
        return False

    @staticmethod
    def _negation_is_the_difference(text_a: str, text_b: str) -> Optional[float]:
        """Similaridade do par quando a negação é a única diferença, senão ``None``."""
        words_a = set(text_a.split())
        words_b = set(text_b.split())
        neg_a = words_a & _NEGATION_MARKERS
        neg_b = words_b & _NEGATION_MARKERS
        if bool(neg_a) == bool(neg_b):
            return None
        stripped_a = " ".join(word for word in text_a.split() if word not in _NEGATION_MARKERS)
        stripped_b = " ".join(word for word in text_b.split() if word not in _NEGATION_MARKERS)
        if not stripped_a or not stripped_b:
            return None
        similarity = difflib.SequenceMatcher(None, stripped_a, stripped_b).ratio()
        return similarity if similarity >= _NEGATION_SIMILARITY else None

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
    def _from_stored(stored: MemoryRecord, superseded_by: Optional[str] = None) -> FederatedFactRecord:
        return FederatedFactRecord(
            id=stored.record_id, fact=stored.content, scope=stored.scope,
            provenance=[str(p.get("uri", "")) for p in stored.provenance if p.get("uri")],
            confidence=stored.confidence, status=stored.status,
            proposed_destination="obsidian" if stored.kind == "decision" else "memory",
            created_at=stored.valid_from, updated_at=stored.valid_from,
            supersedes=list(stored.supersedes), superseded_by=superseded_by,
            metadata=stored.metadata,
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
        self._apply_obsidian(self._from_stored(stored))

    def _apply_obsidian(self, record: FederatedFactRecord) -> None:
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
        self._apply_decisions(self._from_stored(stored))

    def _apply_decisions(self, record: FederatedFactRecord) -> None:
        if not self._is_decision(record):
            return
        title = record.metadata.get("title") or "Decision - %s" % record.id
        self.decisions.record_decision(record.id, title, record.fact, supersedes=record.supersedes)

    def _project_graphrag(self, stored: MemoryRecord) -> None:
        self._apply_graphrag(self._from_stored(stored))

    def _apply_graphrag(self, record: FederatedFactRecord) -> None:
        self._ensure_graphrag_store()
        title = record.metadata.get("title") or "Memory Note - %s" % record.id
        event = KnowledgeEvent.create(
            event_type=KnowledgeEventType.DECISION_RECORDED if self._is_decision(record) else KnowledgeEventType.NOTE_CREATED,
            event_id="memory.changed:" + record.id, uri="memory://" + record.id,
            title=title, content=record.fact, metadata=self._projection_meta(record),
        )
        self.event_bus.publish(event, enqueue=False)

    def _project_embeddings(self, stored: MemoryRecord) -> None:
        """Fourth durable projection: embed the canonical content under the pinned model."""
        self.vector_index.index_text(stored.record_id, stored.content, self.embedder)

    def _vector_search(self, query: str, scopes: Sequence[str], limit: int) -> List[str]:
        """Candidate generation for the retriever; fail-open on any embedder fault.

        Retrieval must degrade to FTS rather than fail the turn, and the retriever
        re-authorizes every returned ID against the journal, so a stale or
        over-broad hit here cannot leak content.
        """
        try:
            query_vector = self.embedder.embed(query)
            return self.vector_index.search(query_vector, limit)
        except Exception:  # noqa: BLE001
            logger.warning("Vector search unavailable; falling back to FTS", exc_info=True)
            return []

    def reindex_vectors(self, *, force: bool = False) -> int:
        """Rebuild vectors for every active canonical record.

        The policy on a model change is: keep the old model's rows (so rollback
        to the previous ``model_id`` is instant) and fill in the new model's rows
        from the journal. ``force=True`` re-embeds even rows that already exist.
        """
        records = [
            (record.record_id, record.content)
            for record in self.canonical_store.list_records(tuple(sorted(VALID_SCOPES)))
        ]
        return self.vector_index.reindex(records, self.embedder, force=force)

    def pending_vector_reindex(self) -> int:
        """Active records that still have no vector under the current model."""
        records = [
            record.record_id
            for record in self.canonical_store.list_records(tuple(sorted(VALID_SCOPES)))
        ]
        return self.vector_index.needs_reindex(records)

    def _legacy_ingest(self, candidate: MemoryCandidate) -> bool:
        """Pre-cutover write path: project directly, with no canonical record.

        This exists solely so ``canonical_writes`` is a reversible flag rather
        than a one-way door. It is refused outright once the cutover has passed
        the point where legacy writers are meant to be off.
        """
        if self.flags.enabled("legacy_writers_disabled"):
            raise FlagError("legacy memory writers are disabled; enable canonical_writes instead")
        record = FederatedFactRecord(
            id=candidate.id or f"fact-{uuid.uuid4().hex[:8]}",
            fact=candidate.fact,
            scope=candidate.scope,
            provenance=list(candidate.provenance),
            confidence=candidate.confidence,
            proposed_destination=candidate.proposed_destination,
            status="consolidated",
            created_at=candidate.created_at or time.time(),
            updated_at=time.time(),
            metadata=getattr(candidate, "metadata", {}) if hasattr(candidate, "metadata") else {},
        )
        candidate.id = record.id
        candidate.status = "consolidated"
        self._apply_obsidian(record)
        self._apply_decisions(record)
        self._apply_graphrag(record)
        self.vector_index.index_text(record.id, record.fact, self.embedder)
        self.metrics.increment("legacy.writes")
        return True

    def legacy_context(self, query: str, access: MemoryAccessContext) -> str:
        """The L3/L4/L5 context, for shadow mode and for the reader rollback path.

        Refused once the cutover has decommissioned legacy readers, so the flag
        has an enforcement point instead of being decorative.
        """
        if self.flags.enabled("legacy_readers_disabled"):
            raise FlagError("legacy memory readers are disabled")
        return LegacyFederatedRetriever(
            obsidian=self.obsidian, decisions=self.decisions, graphrag=self.graphrag
        )(query, access)

    def shadow_retriever(self) -> ShadowRetriever:
        """Legacy-serving retriever that measures the canonical path alongside it."""
        return ShadowRetriever(
            legacy=self.legacy_context,
            canonical_store=self.canonical_store,
            vector_search=self._vector_search if self.flags.enabled("vector_rrf") else None,
            metrics=self.metrics,
        )

    def _sync_stores(self, record: FederatedFactRecord, superseded_ids: List[str]) -> None:
        """Project the committed record.

        With ``projections_via_outbox`` on (the target state) the durable runner
        applies and acknowledges every job, so a crash mid-projection is retried.
        With it off — the pre-cutover behaviour, kept so the flag is genuinely
        reversible — the same projectors run inline and their jobs are
        acknowledged immediately, which is correct but has no durable retry.
        """
        if self.flags.enabled("projections_via_outbox"):
            with Timer(self.metrics, "projection.drain_seconds"):
                self.projection_runner.drain()
            return
        event_id = "memory.changed:" + record.id
        projectors = (
            ("obsidian", lambda: self._apply_obsidian(record)),
            ("decisions", lambda: self._apply_decisions(record)),
            ("graphrag", lambda: self._apply_graphrag(record)),
            ("embeddings", lambda: self.vector_index.index_text(record.id, record.fact, self.embedder)),
        )
        for projection, apply in projectors:
            with Timer(self.metrics, "projection.%s_seconds" % projection):
                apply()
            self.canonical_store.ack(event_id, projection)

    def recover_projections(self, worker_id: str = "fabric-recovery") -> int:
        """Reclaim abandoned leases, then drain everything still pending."""
        reclaimed = self.canonical_store.reclaim_expired_leases()
        if reclaimed:
            self.metrics.increment("expired_leases", reclaimed)
        self.projection_runner.worker_id = worker_id
        return self.projection_runner.drain()

    def auto_supersessions(self) -> List[SupersessionEdge]:
        """Supersessões decididas por heurística, para revisão humana.

        Exclui as ``declared``: se o chamador passou ``supersedes`` explicitamente, ele já
        sabia o que estava fazendo. O que precisa de auditoria é o palpite.
        """
        return [
            edge
            for edge in self.canonical_store.supersession_edges()
            if edge.reason != SUPERSESSION_DECLARED
        ]

    def verify_projections(self) -> Dict[str, ProjectionDrift]:
        """Compara cada projeção com o journal, sem reconstruir nada.

        O outbox responde "há trabalho pendente?"; isto responde a pergunta diferente
        "o que está no disco corresponde ao journal?". As duas importam: um job pode ter
        sido *acknowledged* e ainda assim o artefato estar ausente (projeção apagada à
        mão, banco trocado, nota removida do vault), e nada mais detecta isso.

        Só afirma o que é verificável. Para cada projeção o vínculo é um artefato com
        nome determinístico a partir do ``record_id``:

        * ``obsidian`` — o arquivo em ``20-Architecture/<id>.md`` (decisão) ou
          ``10-Memory/<scope>/<id>.md`` (fato);
        * ``decisions`` — uma decisão no ``DecisionStore`` para cada registro que
          ``_is_decision`` classifica como decisão;
        * ``graphrag`` — ao menos uma entidade em ``entity_sources`` com
          ``memory://<id>``;
        * ``embeddings`` — uma linha em ``memory_vectors`` sob o modelo fixado.

        ``orphans`` são artefatos cujo registro não existe no journal em *nenhum* status —
        projeção de algo que nunca foi canônico, ou lixo de um journal substituído. Um
        registro superseded **não** é órfão: notas antigas e vetores de modelo antigo são
        retidos de propósito (histórico e rollback).
        """
        records = self.canonical_store.list_records(include_superseded=True)
        active = [record for record in records if record.status == "active"]
        known = {record.record_id for record in records}
        report: Dict[str, ProjectionDrift] = {}

        # obsidian -----------------------------------------------------------
        expected_notes = {
            record.record_id: ("20-Architecture/%s.md" % record.record_id if record.kind == "decision"
                               else "10-Memory/%s/%s.md" % (record.scope, record.record_id))
            for record in active
        }
        present_notes = set()
        for record_id, relative in expected_notes.items():
            if (Path(self.obsidian.vault_path) / relative).exists():
                present_notes.add(record_id)
        note_orphans = []
        vault_root = Path(self.obsidian.vault_path)
        if vault_root.exists():
            for path in vault_root.rglob("*.md"):
                stem = path.stem
                if stem in known:
                    continue
                # Only notes the fabric itself wrote can be drift. A human's note is
                # source input, not a projection, and reporting it would drown the
                # signal the operator is looking for.
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if MemoryMigrator.is_projection(content):
                    note_orphans.append(stem)
        report["obsidian"] = ProjectionDrift(
            projection="obsidian",
            expected=len(expected_notes),
            present=len(present_notes),
            missing=tuple(sorted(set(expected_notes) - present_notes)),
            orphans=tuple(sorted(note_orphans)),
        )

        # decisions ----------------------------------------------------------
        expected_decisions = [
            record.record_id for record in active
            if self._is_decision(self._from_stored(record))
        ]
        present_decisions = [
            record_id for record_id in expected_decisions
            if self.decisions.get_decision(record_id) is not None
        ]
        report["decisions"] = ProjectionDrift(
            projection="decisions",
            expected=len(expected_decisions),
            present=len(present_decisions),
            missing=tuple(sorted(set(expected_decisions) - set(present_decisions))),
            orphans=tuple(sorted(
                decision_id for decision_id in self.decisions.decision_ids()
                if decision_id not in known
            )),
        )

        # graphrag -----------------------------------------------------------
        # Read ``graphrag_store`` directly instead of ``_ensure_graphrag_store()``: the
        # latter creates the store when absent, and a verification call must not write.
        if self.graphrag_store is None:
            graph_uris: Set[str] = set()
            graph_unavailable = True
        else:
            graph_uris = set(self.graphrag_store.source_uris())
            graph_unavailable = False
        expected_graph = [record.record_id for record in active]
        present_graph = [
            record_id for record_id in expected_graph if ("memory://" + record_id) in graph_uris
        ]
        report["graphrag"] = ProjectionDrift(
            projection="graphrag",
            expected=len(expected_graph),
            present=len(present_graph),
            missing=tuple(sorted(set(expected_graph) - set(present_graph))),
            orphans=tuple(sorted(
                uri[len("memory://"):] for uri in graph_uris
                if uri.startswith("memory://") and uri[len("memory://"):] not in known
            )),
            detail="store not attached" if graph_unavailable else "",
        )

        # embeddings ---------------------------------------------------------
        indexed = set(self.vector_index.indexed_ids())
        expected_vectors = [record.record_id for record in active]
        present_vectors = [record_id for record_id in expected_vectors if record_id in indexed]
        stale = self.vector_index.needs_reindex(expected_vectors)
        report["embeddings"] = ProjectionDrift(
            projection="embeddings",
            expected=len(expected_vectors),
            present=len(present_vectors),
            missing=tuple(sorted(set(expected_vectors) - set(present_vectors))),
            orphans=tuple(sorted(indexed - known)),
            detail=("%d record(s) need reindex" % stale) if stale else "",
        )

        return report

    def restore_fact(self, record_id: str) -> RestoreOutcome:
        """Desfaz uma supersessão, devolvendo o fato ao conjunto ativo.

        É o par do viés da heurística: como preferimos perder uma contradição a inventar
        uma, o palpite errado precisa ser reversível — e a projeção é refeita a partir do
        journal, então a leitura volta a enxergar o fato no próximo drain.
        """
        outcome = self.canonical_store.restore(record_id)
        if outcome.restored:
            self.metrics.increment("supersessions_restored")
        return outcome

    def fabric_metrics(self) -> Dict[str, Any]:
        """Metrics snapshot with the live outbox backlog and journal counters folded in."""
        snapshot = self.metrics.snapshot(backlog=self.canonical_store.pending_by_projection())
        snapshot["journal"] = self.canonical_store.operational_counters()
        return snapshot

    def get_fact(self, fact_id: str) -> Optional[FederatedFactRecord]:
        """Read through the canonical journal; _facts is only a transient cache."""
        stored = self.canonical_store.get(fact_id)
        if stored is None:
            return None
        return self._from_stored(stored, self.canonical_store.superseded_by_map().get(fact_id))

    @staticmethod
    def _filter_by_access(records: List[FederatedFactRecord], access: Optional[MemoryAccessContext]) -> List[FederatedFactRecord]:
        """Fail-closed ACL filter for aggregated reads.

        Without an access context the caller only ever sees the scopes it asked
        for; with one, every record must additionally prove its tenancy.
        """
        if access is None:
            return records
        return [record for record in records if access.can_read(record.scope, record.metadata)]

    def list_facts(
        self,
        scope: Optional[ScopeType] = None,
        include_superseded: bool = False,
        access: Optional[MemoryAccessContext] = None,
    ) -> List[FederatedFactRecord]:
        if scope and scope not in VALID_SCOPES:
            raise ValueError("Escopo inválido: %r" % scope)
        scopes = (scope,) if scope else tuple(sorted(VALID_SCOPES))
        if access is not None and scope is None:
            scopes = tuple(s for s in access.allowed_scopes() if s in VALID_SCOPES)
        reverse = self.canonical_store.superseded_by_map()
        records = [
            self._from_stored(record, reverse.get(record.record_id))
            for record in self.canonical_store.list_records(scopes, include_superseded)
        ]
        return self._filter_by_access(records, access)

    def query(
        self,
        text: str,
        scope: Optional[ScopeType] = None,
        include_superseded: bool = False,
        access: Optional[MemoryAccessContext] = None,
    ) -> List[FederatedFactRecord]:
        if scope and scope not in VALID_SCOPES:
            raise ValueError("Escopo inválido: %r" % scope)
        scopes = (scope,) if scope else tuple(sorted(VALID_SCOPES))
        if access is not None and scope is None:
            scopes = tuple(s for s in access.allowed_scopes() if s in VALID_SCOPES)
        if not text.strip():
            return self.list_facts(scope=scope, include_superseded=include_superseded, access=access)
        reverse = self.canonical_store.superseded_by_map()
        if not include_superseded:
            records = [
                self._from_stored(record, reverse.get(record.record_id))
                for record in self.canonical_store.search_fts(text, scopes)
            ]
            return self._filter_by_access(records, access)
        needle = self._normalize(text)
        return [
            record
            for record in self.list_facts(scope=scope, include_superseded=True, access=access)
            if needle in self._normalize(record.fact)
        ]

    def close(self) -> None:
        """Encerra threads em background e fecha recursos."""
        self.stop_background_worker()
        self.memory_provider.shutdown()
        if self._owns_graphrag_store and self.graphrag_store is not None:
            self.graphrag_store.close()
        if self._owns_vector_index:
            self.vector_index.close()
        if self._owns_decision_store:
            self.decisions.close()
        self.canonical_store.close()
