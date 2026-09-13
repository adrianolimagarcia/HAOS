"""Arquivo de linhagens/variantes de skills com aceitação não-monotônica (Ponto 5).

WHY: o ciclo de vida de skills do HAOS só sabe medir ATIVIDADE. O curador
(``agent/curator.py:213``) transiciona ``active -> stale -> archived`` a partir de
timestamps e o estado vive em ``skills/.curator_state``/``.usage.json``
(``tools/skill_usage.py:33``); nada por lá compara duas versões da mesma skill,
e nada guarda de onde uma versão veio. Consequência: uma skill que piorou não tem
onde existir (o pipeline de promoção a barra em ``min_eval_score`` e o registro
some), e uma skill que melhorou não tem antepassado — a linhagem é invisível.

Este módulo guarda o outro lado: um arquivo de VARIANTES com genealogia
(``parent_id``/``lineage_root``/``generation``) e duas regras que o HAOS não tinha.

1. Aceitação NÃO-monotônica. Um filho que piora a nota em relação ao pai AINDA
   entra no arquivo, desde que passe no gate de validade. O texto de referência do
   Darwin Gödel Machine diz literalmente que "a functioning child can enter the
   archive even when it performs worse than its parent"; quem ORDENA por nota é a
   seleção (``best_variant``/``select_parent``), não a admissão. O gate de
   promoção do motor procedural (``min_eval_score`` em
   ``hermes/platform/skills/procedural_engine.py:423``) continua sendo o gate de
   PRODUÇÃO: uma variante admitida aqui pode muito bem estar barrada lá — as duas
   perguntas são diferentes ("isto é um programa válido?" vs "isto deve substituir
   o que está no ar?").

   DEFINIÇÃO ESCOLHIDA DE "functioning" (reinterpretação NOSSA, não do texto): o
   texto de referência nunca define o que é um filho "que funciona". Aqui,
   "funciona" = o avaliador determinístico de produção
   (``DeterministicSkillEvaluator``, ``hermes/platform/skills/procedural_evaluator.py:46``)
   devolve ``passed=True``, isto é, NENHUM check crítico reprovado (nome canônico +
   conteúdo executável que compila). A NOTA (``score`` 0..1) NÃO decide admissão —
   ela só ordena. Escolhemos reutilizar o avaliador existente em vez de inventar um
   segundo conceito de "funciona": dois critérios de validade no mesmo repo viram
   dois lugares para discordar.

2. Seleção de pai balanceada. O peso de um pai é proporcional à sua nota e
   penalizado pelo número de filhos que ele JÁ gerou
   (``peso = (score + eps) / (1 + children_count)``), com reposição: cada sorteio é
   independente e o pai sorteado continua no arquivo. Sem a penalidade, a melhor
   variante vira pai de tudo e a linhagem colapsa num único ramo. O sorteio é
   determinístico dado um ``random.Random`` semeado injetado — sem isso o resultado
   não é reproduzível nem testável.

Invariante deste módulo: NADA é deletado. Não existe ``delete()`` de propósito.
O máximo que acontece com uma variante é ``status`` virar ``archived`` (o mesmo
teto do curador, que arquiva em ``skills/.archive/`` em vez de remover), e a linha
continua no store — arquivar tira a variante da seleção por padrão, jamais do
arquivo.

Storage: SQLite próprio em ``$HERMES_HOME/memory/skill_archive.db`` (nunca o
``state.db``, que é de sessões/mensagens). Mesmo formato de store dos outros
módulos do repo (``hermes/platform/observability/event_store.py``,
``hermes/platform/evals/baselines.py``): uma conexão por instância reusada, WAL +
``synchronous=FULL`` quando o filesystem suporta, ``busy_timeout`` alto, RLock.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Union

if TYPE_CHECKING:  # só para type hints: o import de runtime é lazy (ver default_skill_evaluator)
    from hermes.platform.skills.procedural_engine import EvalTestResult
    from hermes.platform.skills.spec import SkillSpec

logger = logging.getLogger("hermes.platform.evolution.skill_archive")

# Status de uma variante DENTRO do arquivo. Não confundir com o status do
# SkillSpec (candidate/sandbox/eval/active/deprecated, em skills/spec.py): aqui a
# pergunta é "esta variante ainda concorre a ser pai?".
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
_VALID_STATUSES = (STATUS_ACTIVE, STATUS_ARCHIVED)

# Piso do peso de seleção: garante que uma variante de nota 0 ainda tenha alguma
# chance de gerar filho (reservatório de diversidade), sem nunca zerar a
# penalidade por filhos.
_SCORE_EPSILON = 0.01

# Espelha event_store.py: escritor concorrente espera, checkpoint PASSIVE periódico.
_BUSY_TIMEOUT_MS = 30_000
_JOURNAL_SIZE_LIMIT = 64 * 1024 * 1024
_CHECKPOINT_EVERY_WRITES = 50


def default_skill_archive_path() -> Path:
    """Caminho canônico do arquivo de variantes do perfil ativo.

    Fica em ``$HERMES_HOME/memory/`` (onde os stores do perfil vivem, ex.
    ``memory/vault_fts/``), NUNCA em ``state.db`` — sessões e variantes de skill
    não compartilham ciclo de vida nem schema.
    """
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()) / "memory" / "skill_archive.db"


def default_skill_evaluator() -> Callable[["SkillSpec"], "EvalTestResult"]:
    """Gate de validade de produção: o avaliador determinístico já usado na promoção.

    Import lazy para ``hermes/platform/evolution/`` não depender de
    ``hermes/platform/skills/`` no import-time (o pacote evolution é importado por
    caminhos que não tocam skills, ex. o CLI de evolução).
    """
    from hermes.platform.skills.procedural_evaluator import DeterministicSkillEvaluator

    return DeterministicSkillEvaluator()


def variant_content_hash(spec: "SkillSpec") -> str:
    """Hash do CONTEÚDO da variante (o que a define como programa, não o estado).

    Só entram campos que descrevem a skill em si; ``status``/``eval_score``/
    timestamps ficam de fora porque mudam sem que a variante mude — se entrassem,
    a mesma variante registrada duas vezes viraria duas linhas.
    """
    payload = {
        "name": spec.name,
        "version": spec.version,
        "description": spec.description,
        "entry_script": spec.entry_script or "",
        "capabilities_required": sorted(spec.capabilities_required or []),
        "dependencies": sorted(spec.dependencies or []),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def variant_id_for(skill: str, content_hash: str) -> str:
    """Id determinístico da variante: mesmo conteúdo ⇒ mesmo id (idempotência).

    Mesmo padrão do ``EvolutionLedger`` (``hermes/platform/evolution/ledger.py:45``):
    registrar duas vezes não duplica o arquivo. Duas variantes de conteúdo idêntico
    são o MESMO programa — a primeira genealogia registrada é a que fica.
    """
    return f"{skill}@{content_hash[:12]}"


@dataclass(frozen=True)
class SkillVariant:
    """Uma variante no arquivo: o conteúdo, a nota medida e a sua genealogia."""

    variant_id: str
    skill: str
    parent_id: Optional[str]
    lineage_root: str
    content_hash: str
    eval_score: float
    children_count: int
    status: str
    generation: int
    created_at: float
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VariantDecision:
    """Resultado de uma tentativa de registrar variante no arquivo.

    ``accepted`` responde à pergunta do gate de VALIDADE, nunca à comparação com o
    pai: ``accepted_below_parent=True`` é uma admissão legítima (não-monotônica) e
    é justamente o caso que este módulo existe para preservar.
    """

    accepted: bool
    variant_id: Optional[str]
    reason: str
    eval_score: float = 0.0
    passed_gate: bool = False
    parent_id: Optional[str] = None
    parent_score: Optional[float] = None
    accepted_below_parent: bool = False
    already_present: bool = False


def selection_weight(variant: SkillVariant) -> float:
    """Peso do pai: proporcional à nota, penalizado pelos filhos que ele já gerou.

    ``filhos`` conta as gerações ANTERIORES dele, não o tamanho da linhagem: é o
    pai sobre-explorado que precisa perder chances na próxima seleção.
    """
    score = max(float(variant.eval_score), 0.0)
    children = max(int(variant.children_count), 0)
    return (score + _SCORE_EPSILON) / (1.0 + children)


class SkillArchive:
    """Arquivo de variantes com genealogia (SQLite; nada é deletado).

    Uso típico (o laço do DGM):

        arquivo = default_skill_archive()
        pai = arquivo.select_parent(skill="pdf-merger", rng=random.Random(7))
        decisao = arquivo.record(filho_spec, parent_id=pai.variant_id if pai else None)
        melhor = arquivo.best_variant(lineage_root=pai.lineage_root if pai else None)

    ``record`` mede o filho com o gate de validade e o admite mesmo quando ele fica
    ABAIXO do pai; ``best_variant`` é quem ordena por nota.
    """

    def __init__(
        self,
        db_path: Union[str, Path] = ":memory:",
        evaluator: Optional[Callable[["SkillSpec"], "EvalTestResult"]] = None,
    ) -> None:
        self.db_path = str(db_path)
        self._evaluator = evaluator or default_skill_evaluator()
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._writes_since_checkpoint = 0
        self._open_connection()
        self._init_db()

    # ------------------------------------------------------------------ #
    # conexão (mesmo desenho do event_store: uma conexão reusada por instância)
    # ------------------------------------------------------------------ #
    def _open_connection(self) -> sqlite3.Connection:
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        if self.db_path != ":memory:":
            try:
                row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
                mode = str(row[0]).lower() if row else ""
            except sqlite3.Error as exc:  # pragma: no cover - filesystem raro
                logger.warning("skill archive WAL indisponível em %s: %s", self.db_path, exc)
                mode = "delete"
            if mode == "wal":
                # O arquivo decide quais variantes existem: durabilidade de decisão
                # por commit (FULL), nunca NORMAL — como o event store.
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute(f"PRAGMA journal_size_limit={_JOURNAL_SIZE_LIMIT}")
            else:
                logger.warning("skill archive %s: journal_mode=%s (WAL n/d)",
                               self.db_path, mode or "unknown")
        self._conn = conn
        return conn

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            return self._open_connection()
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                if self.db_path != ":memory:":
                    self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:  # pragma: no cover - checkpoint é best-effort
                pass
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "SkillArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_connection()
            conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_variants (
                variant_id TEXT PRIMARY KEY,
                skill TEXT NOT NULL,
                parent_id TEXT,
                lineage_root TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                eval_score REAL NOT NULL,
                children_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                generation INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_variants_skill ON skill_variants(skill);")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_variants_lineage ON skill_variants(lineage_root);")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_variants_parent ON skill_variants(parent_id);")
            conn.commit()

    # ------------------------------------------------------------------ #
    # escrita: admissão não-monotônica
    # ------------------------------------------------------------------ #
    def record(
        self,
        spec: "SkillSpec",
        *,
        parent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> VariantDecision:
        """Tenta admitir a variante no arquivo. A nota NÃO decide a admissão.

        ``parent_id=None`` significa raiz de linhagem (a primeira variante de um
        ramo novo); quem quer o pai "natural" de produção usa
        ``record_promoted_skill``, que deriva o pai da melhor variante da skill.

        Recusas (``accepted=False``) são o gate de validade reprovado, spec sem
        nome, ou pai inexistente no arquivo — nunca "nota menor que a do pai".
        """
        skill = (spec.name or "").strip()
        if not skill:
            return VariantDecision(False, None, "spec sem nome: variante não identificável")

        result = self._evaluator(spec)
        if not result.passed:
            return VariantDecision(
                False, None,
                f"gate de validade reprovado ({result.test_id}): {result.message}",
                eval_score=float(result.score), passed_gate=False, parent_id=parent_id,
            )

        content_hash = variant_content_hash(spec)
        variant_id = variant_id_for(skill, content_hash)

        with self._lock:
            parent = self._fetch(parent_id) if parent_id else None
            if parent_id and parent is None:
                return VariantDecision(
                    False, variant_id,
                    f"pai {parent_id!r} não existe no arquivo: genealogia órfã não entra",
                    eval_score=float(result.score), passed_gate=True, parent_id=parent_id,
                )

            existing = self._fetch(variant_id)
            if existing is not None:
                # Idempotente: re-registrar a MESMA variante não infla children_count
                # do pai (senão a penalidade de seleção seria uma função do número
                # de tentativas de registro, não de gerações realmente geradas).
                return VariantDecision(
                    True, variant_id, "variante já presente no arquivo (conteúdo idêntico)",
                    eval_score=existing.eval_score, passed_gate=True,
                    parent_id=existing.parent_id,
                    parent_score=parent.eval_score if parent else None,
                    already_present=True,
                )

            score = float(result.score)
            row_metadata = {
                "version": spec.version,
                "description": spec.description,
                "capabilities_required": list(spec.capabilities_required or []),
                "evaluator": result.test_id,
                "eval_message": result.message,
            }
            if metadata:
                row_metadata.update(metadata)

            generation = (parent.generation + 1) if parent else 0
            lineage_root = parent.lineage_root if parent else variant_id
            below_parent = parent is not None and score < parent.eval_score

            conn = self._get_connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO skill_variants (variant_id, skill, parent_id, lineage_root,
                                                content_hash, eval_score, children_count, status,
                                                generation, created_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        variant_id, skill, parent.variant_id if parent else None, lineage_root,
                        content_hash, score, STATUS_ACTIVE, generation, time.time(),
                        json.dumps(row_metadata, ensure_ascii=False),
                    ),
                )
                if parent is not None:
                    conn.execute(
                        "UPDATE skill_variants SET children_count = children_count + 1 "
                        "WHERE variant_id = ?",
                        (parent.variant_id,),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            self._maybe_checkpoint(conn)

        reason = "admitida no arquivo"
        if parent is None:
            reason += " (raiz de linhagem)"
        elif below_parent:
            reason += (
                f" mesmo abaixo do pai ({score:.3f} < {parent.eval_score:.3f}): "
                "admissão não-monotônica, a ordenação é da seleção"
            )
        return VariantDecision(
            True, variant_id, reason, eval_score=score, passed_gate=True,
            parent_id=parent.variant_id if parent else None,
            parent_score=parent.eval_score if parent else None,
            accepted_below_parent=below_parent,
        )

    def _maybe_checkpoint(self, conn: sqlite3.Connection) -> None:
        self._writes_since_checkpoint += 1
        if self._writes_since_checkpoint < _CHECKPOINT_EVERY_WRITES:
            return
        self._writes_since_checkpoint = 0
        if self.db_path == ":memory:":
            return
        try:
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error as exc:  # pragma: no cover - concorrência rara
            logger.warning("skill archive checkpoint falhou: %s", exc)

    def mark_skill_status(self, skill: str, status: str) -> int:
        """Transição de status para TODAS as variantes de uma skill; devolve quantas mudaram.

        É o gancho do curador nos dois sentidos do ciclo de vida dele: ao arquivar
        uma skill por inatividade (``agent/curator.py:213`` ->
        ``tools/skill_usage.archive_skill``) as variantes deixam de concorrer a pai;
        ao REATIVAR uma skill (``reactivated``) voltam a concorrer. Nenhuma linha é
        removida — arquivar é o teto destrutivo aqui também, e a variante arquivada
        continua legível (``get``/``lineage``/``variants(status=...)``).
        """
        if status not in _VALID_STATUSES:
            raise ValueError(f"status inválido: {status!r} (use {'|'.join(_VALID_STATUSES)})")
        with self._lock:
            conn = self._get_connection()
            cur = conn.execute(
                "UPDATE skill_variants SET status = ? WHERE skill = ? AND status != ?",
                (status, skill, status),
            )
            conn.commit()
            return int(cur.rowcount)

    # ------------------------------------------------------------------ #
    # seleção de pai (balanceada, determinística dado o rng semeado)
    # ------------------------------------------------------------------ #
    def select_parent(
        self,
        *,
        skill: Optional[str] = None,
        lineage_root: Optional[str] = None,
        rng: Optional[random.Random] = None,
        include_archived: bool = False,
    ) -> Optional[SkillVariant]:
        """Sorteia a variante que vai gerar a próxima geração ("with replacement").

        Peso ∝ nota do pai, penalizado por ``children_count`` (ver
        ``selection_weight``). A amostragem é COM REPOSIÇÃO: o sorteado continua no
        arquivo e pode ser sorteado de novo; é justamente isso que permite uma
        linhagem pior voltar a gerar filhos em vez de a melhor monopolizar tudo.

        ``rng`` injetado torna o resultado reproduzível (mesma seed ⇒ mesma
        sequência de pais); sem ele usa-se um ``random.Random()`` novo, semeado pelo
        sistema. Arquivados ficam fora por padrão (não estão em produção), mas
        ``include_archived=True`` sorteia do reservatório inteiro — o arquivo
        guarda a diversidade mesmo quando a skill saiu de uso.
        """
        candidates = self.variants(
            skill=skill,
            status=None if include_archived else STATUS_ACTIVE,
            lineage_root=lineage_root,
        )
        if not candidates:
            return None
        rng = rng or random.Random()
        weights = [selection_weight(v) for v in candidates]
        total = sum(weights)
        if total <= 0:  # pragma: no cover - _SCORE_EPSILON torna o total > 0
            return rng.choice(candidates)
        target = rng.random() * total
        accumulated = 0.0
        for variant, weight in zip(candidates, weights):
            accumulated += weight
            if target < accumulated:
                return variant
        return candidates[-1]

    # ------------------------------------------------------------------ #
    # leitura (melhor variante por linhagem, genealogia, membros)
    # ------------------------------------------------------------------ #
    def best_variant(
        self,
        *,
        lineage_root: Optional[str] = None,
        skill: Optional[str] = None,
        include_archived: bool = True,
    ) -> Optional[SkillVariant]:
        """Melhor variante por nota (empate: a mais antiga, isto é, o antepassado).

        É a leitura que ORDENA — o oposto da admissão, que não olha para a nota do
        pai. Sem filtro devolve o melhor do arquivo inteiro; com ``lineage_root``
        devolve o melhor da linhagem (o "current best" daquele ramo).
        """
        ranked = self.variants(
            skill=skill,
            status=None if include_archived else STATUS_ACTIVE,
            lineage_root=lineage_root,
        )
        if not ranked:
            return None
        return max(ranked, key=lambda v: (v.eval_score, -v.created_at, v.variant_id))

    def lineage(self, lineage_root: str) -> List[SkillVariant]:
        """Todas as variantes de uma linhagem, da mais antiga para a mais recente."""
        return self.variants(lineage_root=lineage_root)

    def lineage_roots(self, skill: Optional[str] = None) -> List[str]:
        """Raízes de linhagem (uma por ramo), na ordem de criação."""
        with self._lock:
            rows = self._get_connection().execute(
                """
                SELECT lineage_root, MIN(created_at) AS first_seen
                FROM skill_variants
                WHERE (? IS NULL OR skill = ?)
                GROUP BY lineage_root ORDER BY first_seen ASC, lineage_root ASC
                """,
                (skill, skill),
            ).fetchall()
            return [row["lineage_root"] for row in rows]

    def children(self, variant_id: str) -> List[SkillVariant]:
        """Filhos diretos de uma variante (a evidência da penalidade de seleção)."""
        return self.variants(parent_id=variant_id)

    def variants(
        self,
        *,
        skill: Optional[str] = None,
        status: Optional[str] = None,
        lineage_root: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> List[SkillVariant]:
        """Membros do arquivo, filtráveis por skill/status/linhagem/pai.

        Ordem determinística (``created_at``, ``variant_id``): a seleção ponderada
        depende da ordem para ser reproduzível com o mesmo rng.
        """
        clauses: List[str] = []
        params: List[Any] = []
        for column, value in (
            ("skill", skill), ("status", status),
            ("lineage_root", lineage_root), ("parent_id", parent_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._get_connection().execute(
                f"SELECT * FROM skill_variants {where} ORDER BY created_at ASC, variant_id ASC",
                params,
            ).fetchall()
            return [self._row_to_variant(row) for row in rows]

    def get(self, variant_id: str) -> Optional[SkillVariant]:
        """Variante por id (``None`` se não existir)."""
        return self._fetch(variant_id)

    def count(self, skill: Optional[str] = None) -> int:
        """Quantas variantes o arquivo guarda (o arquivo nunca diminui sozinho)."""
        return len(self.variants(skill=skill))

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    def _fetch(self, variant_id: Optional[str]) -> Optional[SkillVariant]:
        if not variant_id:
            return None
        with self._lock:
            row = self._get_connection().execute(
                "SELECT * FROM skill_variants WHERE variant_id = ?", (variant_id,)
            ).fetchone()
        return self._row_to_variant(row) if row is not None else None

    @staticmethod
    def _row_to_variant(row: sqlite3.Row) -> SkillVariant:
        return SkillVariant(
            variant_id=row["variant_id"],
            skill=row["skill"],
            parent_id=row["parent_id"],
            lineage_root=row["lineage_root"],
            content_hash=row["content_hash"],
            eval_score=float(row["eval_score"]),
            children_count=int(row["children_count"]),
            status=row["status"],
            generation=int(row["generation"]),
            created_at=float(row["created_at"]),
            metadata=json.loads(row["metadata"] or "{}"),
        )


def default_skill_archive(evaluator: Optional[Callable[[Any], Any]] = None) -> SkillArchive:
    """Arquivo de variantes do perfil ativo (``$HERMES_HOME/memory/skill_archive.db``).

    Mesma razão de ``default_procedural_registry``/``get_event_store``: uma
    instância ``:memory:`` por default daria um arquivo novo e vazio por processo —
    genealogia que some ao terminar o comando não é genealogia.
    """
    return SkillArchive(db_path=default_skill_archive_path(), evaluator=evaluator)


def record_promoted_skill(
    spec: "SkillSpec",
    *,
    parent_id: Optional[str] = None,
    archive: Optional[SkillArchive] = None,
) -> Optional[VariantDecision]:
    """PONTO DE INTEGRAÇÃO DE PRODUÇÃO: chame DEPOIS de uma promoção bem-sucedida.

    Ex.: ``hermes/platform/evolution/ouroboros_lifecycle.py:408`` (logo após
    ``skill_pipeline.evaluate_and_activate`` devolver ``ok``) ou
    ``hermes_cli/haos_cmd.py:237`` (após ``run_full_pipeline``). Uma linha:

        record_promoted_skill(candidate_spec)

    Sem ``parent_id`` explícito o pai é a melhor variante JÁ arquivada daquela skill
    (a variante que estava no ar quando esta foi promovida), então a genealogia se
    forma sozinha; na primeira promoção de uma skill não há pai e a variante é raiz
    de linhagem.

    Fail-soft DELIBERADO: falha de store (disco cheio, arquivo corrompido) é logada
    e devolve ``None`` — genealogia é registro do que aconteceu, e não pode derrubar
    a promoção de uma skill que já passou em todos os gates. Repare que o fail-soft
    cobre só erro de STORE: recusa de gate de validade continua sendo recusa
    (``accepted=False``), nunca silêncio.
    """
    try:
        target = archive or default_skill_archive()
        if parent_id is None:
            best = target.best_variant(skill=spec.name, include_archived=False)
            parent_id = best.variant_id if best else None
        return target.record(spec, parent_id=parent_id)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("skill archive indisponível ao registrar variante de %s: %s", spec.name, exc)
        return None


def mark_skill_status(
    skill: str, status: str, *, archive: Optional[SkillArchive] = None
) -> int:
    """Wrapper de módulo para o gancho do curador nos DOIS sentidos do ciclo de vida.

    ``agent/curator.py::_sync_archive_status`` chama isto ao arquivar (variantes
    deixam de concorrer a pai) e ao reativar (voltam a concorrer). O status é
    validado por ``SkillArchive.mark_skill_status``; nenhuma linha é removida.
    """
    target = archive or default_skill_archive()
    return target.mark_skill_status(skill, status)


def mark_skill_archived(skill: str, *, archive: Optional[SkillArchive] = None) -> int:
    """PONTO DE INTEGRAÇÃO DO CURADOR: skill arquivada sai da seleção de pai.

    Chame quando o curador arquivar uma skill por inatividade
    (``agent/curator.py:213`` -> ``tools/skill_usage.archive_skill``). Devolve
    quantas variantes passaram a ``archived``. Nenhuma linha é removida — arquivar
    é o teto destrutivo aqui também.
    """
    return mark_skill_status(skill, STATUS_ARCHIVED, archive=archive)


__all__ = [
    "STATUS_ACTIVE",
    "STATUS_ARCHIVED",
    "SkillArchive",
    "SkillVariant",
    "VariantDecision",
    "default_skill_archive",
    "default_skill_archive_path",
    "default_skill_evaluator",
    "mark_skill_archived",
    "mark_skill_status",
    "record_promoted_skill",
    "selection_weight",
    "variant_content_hash",
    "variant_id_for",
]
