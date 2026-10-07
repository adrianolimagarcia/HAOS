import json
import inspect
import dataclasses
from pathlib import Path

from evals.gbrain_memory.eval_harness import (
    verify_corpus_integrity,
    load_corpus,
    load_manifest,
)


def test_eval_corpus_integrity_manifest():
    """Garante que o corpus congelado não foi adulterado e bate com o hash assinado no manifesto."""
    assert verify_corpus_integrity() is True, "Corpus congelado diverge do SHA256 no manifesto!"


def test_eval_corpus_structure_and_categories():
    """Valida a estrutura de cada caso de teste e presença de todas as 5 categorias de lacunas."""
    corpus = load_corpus()
    cases = corpus.get("cases", [])
    assert len(cases) == 12

    expected_categories = {
        "temporal_validity",
        "observed_vs_recorded",
        "frescor_conflito_transparencia",
        "dream_cursor_reopened_session",
        "conflict_surfacing",
    }
    found_categories = {c["category"] for c in cases}
    assert found_categories == expected_categories

    for c in cases:
        assert "id" in c
        assert "category" in c
        assert "title" in c
        assert "description" in c
        assert "expected_behavior" in c
        assert "haos_current_behavior" in c
        assert "gap_ref" in c


def test_temporal_validity_gap_verification():
    """Invariante pós-Etapa 2 (fecha GAP-01): search_fts aplica filtro de vigência temporal.

    Era detector de lacuna na Etapa 0 (afirmava ausência de valid_until no search_fts);
    virou contrato de presença com a implementação do filtro temporal [valid_from, valid_until).
    """
    from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore

    fts_src = inspect.getsource(CanonicalMemoryStore.search_fts)
    assert "r.status='active'" in fts_src
    # Confirma que valid_until e valid_from são checados no SQL/filtro
    assert "valid_until" in fts_src
    assert "valid_from" in fts_src



def test_observed_at_gap_verification():
    """Invariante pós-Etapa 1 (fecha GAP-02): MemoryRecord possui observed_at com
    semântica própria (default = valid_from) e a migração aditiva faz backfill.

    Era detector de lacuna na Etapa 0 (afirmava a ausência); virou contrato de
    presença quando a Etapa 1 implementou o campo.
    """
    from hermes.platform.context.memory.canonical_store import MemoryRecord

    fields = [f.name for f in dataclasses.fields(MemoryRecord)]
    assert "valid_from" in fields
    assert "observed_at" in fields
    # Semântica: observed_at opcional, default = valid_from (nunca inventado)
    rec_default = MemoryRecord.__dataclass_fields__["observed_at"].default
    assert rec_default is None


def test_render_freshness_gap_verification():
    """Verifica evidência de código do GAP-03: _render() formata sem metadados de frescor ou ressalvas."""
    from hermes.platform.context.memory.retrieval import HybridMemoryRetriever

    retriever_src = inspect.getsource(HybridMemoryRetriever._render)
    assert '[memory:%s scope=%s provenance=%s]' in retriever_src
    # Confirma que não há injeção de data, validade ou confiança
    assert "recente" not in retriever_src
    assert "valid_until" not in retriever_src
    assert "confidence" not in retriever_src


def test_dream_cursor_gap_verification():
    """Verifica evidência de código do GAP-04: run_dream filtra sessões pelo cursor."""
    from hermes.platform.memory.dream import DreamConsolidator

    dream_src = inspect.getsource(DreamConsolidator.run_dream)
    assert "cursor" in dream_src
    assert "get_cursor" in dream_src
    assert "set_cursor" in dream_src
    assert "list_recent_sessions_bounded" in dream_src
    # Confirma ausência de verificação em last_message_at
    assert "last_message_at" not in dream_src


def test_conflict_destination_gap_verification():
    """Verifica evidência de código do GAP-05: conflitos do dream são enviados apenas para o staging."""
    from hermes.platform.memory.dream import DreamConsolidator

    dream_src = inspect.getsource(DreamConsolidator.run_dream)
    assert "STAGE_REASON_CONFLICT" in dream_src
    assert "self.router.stage_candidate(candidate, reason=STAGE_REASON_CONFLICT)" in dream_src
    # Confirma ausência de notificação ou alerta ao operador
    assert "notify" not in dream_src
    assert "alert" not in dream_src
    assert "inbox" not in dream_src
