"""TDD: decaimento de recência determinístico no retrieval (absorvido do CAMEL).

score_final = relevance * keep_rate ** idade_em_dias, records pinados protegidos.
Unidade da função pura + integração no ponto real de ranking (HybridMemoryRetriever).
"""
import time

import pytest

from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore
from hermes.platform.context.memory.recency import (
    DEFAULT_KEEP_RATE,
    apply_recency,
    age_days,
    is_pinned,
    keep_rate_from_env,
)
from hermes.platform.context.memory.retrieval import HybridMemoryRetriever

DAY = 86400.0


# ---------------------------------------------------------------- unit: função pura
def test_decay_applies_keep_rate_to_the_power_of_age():
    # 14 dias com keep_rate 0.95 -> 0.95**14
    assert apply_recency(1.0, age_days=14.0, keep_rate=0.95) == pytest.approx(0.95 ** 14)
    assert apply_recency(0.5, age_days=3.0, keep_rate=0.90) == pytest.approx(0.5 * 0.90 ** 3)


def test_age_zero_is_pure_relevance():
    assert apply_recency(0.777, age_days=0.0, keep_rate=0.95) == pytest.approx(0.777)


def test_keep_rate_one_is_noop():
    for age in (0.0, 1.5, 1000.0):
        assert apply_recency(0.42, age_days=age, keep_rate=1.0) == pytest.approx(0.42)


def test_pinned_record_is_protected_from_decay():
    # peso 1.0: score sobrevive intacto qualquer que seja a idade
    assert apply_recency(0.3, age_days=365.0, keep_rate=0.95, pinned=True) == pytest.approx(0.3)


def test_negative_age_is_clamped_to_zero():
    # futuro (relógio desajustado) nunca deve inflar o score acima da relevância
    assert apply_recency(0.6, age_days=-50.0, keep_rate=0.95) == pytest.approx(0.6)


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5, float("nan"), float("inf")])
def test_invalid_keep_rate_is_rejected(bad):
    with pytest.raises(ValueError):
        apply_recency(1.0, age_days=1.0, keep_rate=bad)


def test_age_days_math_against_injected_now():
    now = 1_700_000_000.0
    assert age_days(valid_from=now, now=now) == pytest.approx(0.0)
    assert age_days(valid_from=now - 2 * DAY, now=now) == pytest.approx(2.0)
    # futuro clampado a 0 (determinismo: nada de idade negativa)
    assert age_days(valid_from=now + DAY, now=now) == pytest.approx(0.0)


# ---------------------------------------------------------------- unit: config por env
def test_env_default_is_095(monkeypatch):
    monkeypatch.delenv("HAOS_MEMORY_RECENCY_KEEP_RATE", raising=False)
    assert keep_rate_from_env() == pytest.approx(DEFAULT_KEEP_RATE)
    assert DEFAULT_KEEP_RATE == pytest.approx(0.95)


def test_env_override(monkeypatch):
    monkeypatch.setenv("HAOS_MEMORY_RECENCY_KEEP_RATE", "0.99")
    assert keep_rate_from_env() == pytest.approx(0.99)
    monkeypatch.setenv("HAOS_MEMORY_RECENCY_KEEP_RATE", "1.0")
    assert keep_rate_from_env() == pytest.approx(1.0)  # 1.0 desliga o decay


@pytest.mark.parametrize("bad", ["0", "0.0", "-1", "1.01", "abc", ""])
def test_env_invalid_falls_back_to_default(monkeypatch, bad):
    monkeypatch.setenv("HAOS_MEMORY_RECENCY_KEEP_RATE", bad)
    assert keep_rate_from_env() == pytest.approx(DEFAULT_KEEP_RATE)


# ---------------------------------------------------------------- unit: marcador de pin
def test_is_pinned_reads_metadata_and_system_kind():
    class R:
        def __init__(self, metadata=None, kind="fact"):
            self.metadata = metadata or {}
            self.kind = kind

    assert is_pinned(R({"pin": True})) is True
    assert is_pinned(R({"pinned": True})) is True
    assert is_pinned(R(kind="system")) is True
    assert is_pinned(R()) is False           # default False
    assert is_pinned(R({"pin": False})) is False
    assert is_pinned(R({"pin": "no"})) is False


# ---------------------------------------------------------------- integration: ranking real
def _store_with(tmp_path, records):
    store = CanonicalMemoryStore(tmp_path / "memory.db")
    for key, content, valid_from, metadata, kind in records:
        store.append(content=content, scope="project", idempotency_key=key,
                     valid_from=valid_from, metadata=metadata, kind=kind)
    return store


def test_same_relevance_orders_by_recency(tmp_path):
    now = time.time()
    # dois records com conteúdo-idêntico em relevância FTS (mesma frase-alvo),
    # um velho (30 dias) e um novo: o novo deve vencer quando o decay está ligado.
    store = _store_with(tmp_path, [
        ("old", "procedure alpha old", now - 30 * DAY, {}, "fact"),
        ("new", "procedure alpha new", now, {}, "fact"),
    ])
    retriever = HybridMemoryRetriever(store, keep_rate=0.95)
    hits = retriever.retrieve("procedure alpha", ["project"], now=now)
    assert [h.record.record_id for h in hits] == ["new", "old"]
    assert hits[0].score > hits[1].score
    store.close()


def test_pinned_beats_fresher_unpinned(tmp_path):
    now = time.time()
    # pinned é o mais relevante na base FTS (tf maior: rank 1 garantido, sem depender de
    # tiebreak). Sem pin, 100 dias de decay (0.95**100 ≈ 0.006) o jogaria abaixo do fresh;
    # com pin, a proteção mantém o fundacional no topo.
    store = _store_with(tmp_path, [
        ("pinned", "procedure alpha procedure alpha pinned", now - 100 * DAY, {"pin": True}, "fact"),
        ("fresh", "procedure alpha fresh", now, {}, "fact"),
    ])
    retriever = HybridMemoryRetriever(store, keep_rate=0.95)
    hits = retriever.retrieve("procedure alpha", ["project"], now=now)
    assert [h.record.record_id for h in hits] == ["pinned", "fresh"]
    # e sem o pin o mesmo corpus inverteria a ordem: prova de que a proteção é o que decide
    store2 = _store_with(tmp_path / "2.db", [
        ("pinned", "procedure alpha procedure alpha pinned", now - 100 * DAY, {}, "fact"),
        ("fresh", "procedure alpha fresh", now, {}, "fact"),
    ])
    unpin_hits = HybridMemoryRetriever(store2, keep_rate=0.95).retrieve("procedure alpha", ["project"], now=now)
    assert [h.record.record_id for h in unpin_hits] == ["fresh", "pinned"]
    store.close()
    store2.close()


def test_keep_rate_one_is_noop_in_ranking(tmp_path):
    now = time.time()
    store = _store_with(tmp_path, [
        ("old", "procedure alpha old", now - 30 * DAY, {}, "fact"),
        ("new", "procedure alpha new", now, {}, "fact"),
    ])
    retriever = HybridMemoryRetriever(store, keep_rate=1.0)
    hits = retriever.retrieve("procedure alpha", ["project"], now=now)
    # no-op: os scores são exatamente os pesos RRF crus (1/(60+rank)), sem multiplicador.
    assert {h.record.record_id for h in hits} == {"old", "new"}
    assert hits[0].score == pytest.approx(1.0 / 61.0)
    assert hits[1].score == pytest.approx(1.0 / 62.0)
    store.close()


def test_env_config_reaches_retriever(tmp_path, monkeypatch):
    monkeypatch.setenv("HAOS_MEMORY_RECENCY_KEEP_RATE", "0.5")
    now = time.time()
    store = _store_with(tmp_path, [
        ("old", "procedure alpha old", now - 10 * DAY, {}, "fact"),
        ("new", "procedure alpha new", now, {}, "fact"),
    ])
    retriever = HybridMemoryRetriever(store)  # sem keep_rate explícito -> env
    assert retriever.keep_rate == pytest.approx(0.5)
    hits = retriever.retrieve("procedure alpha", ["project"], now=now)
    assert hits[0].record.record_id == "new"
    store.close()


def test_invalid_env_never_breaks_retriever(tmp_path, monkeypatch):
    monkeypatch.setenv("HAOS_MEMORY_RECENCY_KEEP_RATE", "banana")
    store = _store_with(tmp_path, [("x", "procedure x", time.time(), {}, "fact")])
    retriever = HybridMemoryRetriever(store)
    assert retriever.keep_rate == pytest.approx(DEFAULT_KEEP_RATE)
    store.close()
