"""Testes de contrato do P12 — escrita em delta (append-only) no staging.

Interpretação documentada (a spec só diz "escrita em delta; veja os stores...
o que já é append-only"): o staging store ganha um JORNAL ADITIVO append-only
``pending_candidates.delta.jsonl`` — cada mutação (stage_candidate /
mark_promoted / expire) ADICIONA um registro {seq, op, key, record, ts} em vez
de reescrever histórico. O SNAPSHOT continua autoritativo (P5 assina
pending_candidates.json; o teste _age_record escreve no snapshot direto e o
expire precisa enxergar — re-play do jornal em load() quebraria os dois), e o
jornal é o degrau de RECUPERAÇÃO: com o snapshot ausente/corrompido,
``recover_from_delta()`` reconstrói o snapshot a partir do jornal. Compactação:
ao atingir 128 registros o jornal é truncado (o snapshot recém-gravado já
contém tudo). O jornal NUNCA entra em canonical_files()/validate_json_index
(superfície de contrato do P5 inalterada).

Nenhum teste lê texto de código-fonte; nenhum é change-detector.
"""

from __future__ import annotations

import json
import time

import pytest

from hermes.platform.context.memory.candidate import MemoryCandidate
from hermes.platform.context.memory.staging import (
    DELTA_COMPACT_THRESHOLD,
    EXPIRED,
    PENDING,
    PROMOTED,
    MemoryStagingStore,
    STAGING_FILENAME,
    candidate_key,
)

LESSON = "a lesson repeated across sessions must be reinforced"


def _cand(fact: str = LESSON, confidence: float = 0.3) -> MemoryCandidate:
    return MemoryCandidate(fact=fact, source_uri="session://delta", confidence=confidence)


# ── Jornal aditivo: cada mutação ADICIONA um registro ────────────────────────

def test_delta_file_existe_no_home(tmp_path):
    store = MemoryStagingStore(tmp_path)
    assert store.delta_file == tmp_path / "pending_candidates.delta.jsonl"


def test_stage_append_um_registro_upsert(tmp_path):
    store = MemoryStagingStore(tmp_path)
    store.stage_candidate(_cand())
    lines = store.delta_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["op"] == "upsert"
    assert rec["seq"] == 1
    assert rec["key"] == candidate_key(LESSON)
    assert rec["record"]["status"] == PENDING
    assert set(rec) == {"seq", "op", "key", "record", "ts"}


def test_cada_mutacao_adiciona_linha_sem_reescrever(tmp_path):
    store = MemoryStagingStore(tmp_path)
    store.stage_candidate(_cand())
    store.stage_candidate(_cand("another fact"))
    store.mark_promoted(candidate_key(LESSON), doc_path="okf/x.md")
    lines = store.delta_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    # Ordem append-only preservada (seqs crescentes, sem reescrita).
    seqs = [json.loads(ln)["seq"] for ln in lines]
    assert seqs == [1, 2, 3]


def test_expire_registra_cada_demovido_no_jornal(tmp_path):
    store = MemoryStagingStore(tmp_path)
    store.stage_candidate(_cand())
    key = candidate_key(LESSON)
    # Envelhece via snapshot direto (mesma tática do teste P5 _age_record):
    # o load() continua vendo o SNAPSHOT — o jornal nunca sobrepõe.
    data = json.loads(store.index_file.read_text(encoding="utf-8"))
    data[key]["last_seen_at"] = time.time() - 31 * 86400
    store.index_file.write_text(json.dumps(data), encoding="utf-8")
    assert store.expire(now=time.time()) == 1
    lines = store.delta_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    last = json.loads(lines[-1])
    assert last["op"] == "expire"
    assert last["key"] == key
    assert last["record"]["status"] == EXPIRED


# ── load() continua SNAPSHOT-autoritativo ────────────────────────────────────

def test_load_ignora_jornal_e_ve_o_snapshot_direto(tmp_path):
    store = MemoryStagingStore(tmp_path)
    store.stage_candidate(_cand())
    key = candidate_key(LESSON)
    # Escrita DIRETA no snapshot (sem passar pelo store) + jornal divergente
    # (diz que o registro foi promovido) — o load() tem que ver o snapshot.
    data = json.loads(store.index_file.read_text(encoding="utf-8"))
    data[key]["status"] = PROMOTED
    store.index_file.write_text(json.dumps(data), encoding="utf-8")
    store.delta_file.write_text(
        json.dumps({"seq": 99, "op": "promote", "key": key,
                    "record": {**data[key], "status": PENDING}, "ts": time.time()})
        + "\n",
        encoding="utf-8",
    )
    assert store.get(key)["status"] == PROMOTED


# ── Recuperação a partir do jornal ───────────────────────────────────────────

def test_recover_reconstroi_snapshot_ausente(tmp_path):
    store = MemoryStagingStore(tmp_path)
    store.stage_candidate(_cand())
    store.stage_candidate(_cand("fact b"))
    store.mark_promoted(candidate_key(LESSON), doc_path="okf/x.md")
    store.index_file.unlink()  # snapshot perdido; jornal intacto
    assert store.load() == {}
    assert store.recover_from_delta() is True
    assert store.get(candidate_key(LESSON))["status"] == PROMOTED
    assert store.get(candidate_key("fact b"))["status"] == PENDING
    assert store.index_file.exists()


def test_recover_reconstroi_snapshot_corrompido(tmp_path):
    store = MemoryStagingStore(tmp_path)
    store.stage_candidate(_cand())
    store.index_file.write_text("{json quebrado", encoding="utf-8")
    assert store.recover_from_delta() is True
    assert store.get(candidate_key(LESSON))["status"] == PENDING


def test_recover_sem_jornal_nao_inventa_snapshot(tmp_path):
    store = MemoryStagingStore(tmp_path)
    assert store.recover_from_delta() is False
    assert not store.index_file.exists()


# ── Compactação: jornal truncado após 128 registros ──────────────────────────

def test_compactacao_trunca_jornal_apos_limiar(tmp_path):
    store = MemoryStagingStore(tmp_path)
    n = DELTA_COMPACT_THRESHOLD + 2
    for i in range(n):
        store.stage_candidate(_cand(f"fact number {i:03d}"))
    remaining = store.delta_file.read_text(encoding="utf-8").strip().splitlines()
    # No write #128 o jornal truncou (snapshot recém-gravado contém tudo) e
    # recomeçou em seq 1: sobraram exatamente os registros pós-compactação.
    assert len(remaining) == n - DELTA_COMPACT_THRESHOLD
    # O jornal recomeçou: a primeira linha sobrevivente é seq 1 de novo.
    assert json.loads(remaining[0])["seq"] == 1
    # Nada se perdeu: o snapshot tem todos os candidatos.
    assert len(store.list_pending()) == n


# ── E2E do fluxo com o jornal refletindo as transições ───────────────────────

def test_fluxo_completo_jornal_reflete_transicoes(tmp_path):
    store = MemoryStagingStore(tmp_path)
    key = candidate_key(LESSON)
    store.stage_candidate(_cand())
    store.mark_promoted(key, doc_path="okf/l.md")
    store.stage_candidate(_cand(LESSON))  # reforço reaparece → volta a pending
    ops = [json.loads(ln)["op"] for ln in store.delta_file.read_text(encoding="utf-8").strip().splitlines()]
    assert ops == ["upsert", "promote", "upsert"]
    assert store.get(key)["status"] == PENDING
    assert store.is_promoted(key) is False