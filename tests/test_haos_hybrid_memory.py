"""Tests for OKF parser and Hybrid Knowledge Router in HAOS."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from hermes.platform.memory.okf import OKFStore
from hermes.platform.memory.hybrid_router import HybridKnowledgeRouter


@pytest.fixture
def temp_okf_dir(tmp_path: Path) -> Path:
    okf_dir = tmp_path / "okf"
    okf_dir.mkdir(parents=True, exist_ok=True)
    return okf_dir


def test_okf_save_and_find_deterministic(temp_okf_dir: Path) -> None:
    store = OKFStore(temp_okf_dir)
    
    # Save a canonical metric
    doc = store.save_document(
        title="Monthly Churn Rate",
        content="Churn Rate = (Lost Customers / Starting Customers) * 100",
        doc_type="metric",
        tags=["revenue", "kpi"],
        owner="finance",
    )
    assert doc.title == "Monthly Churn Rate"
    assert doc.filepath.exists()

    # Find by exact title
    found = store.find_deterministic("Monthly Churn Rate")
    assert found is not None
    assert "Churn Rate =" in found.body

    # Find by tag
    found_by_tag = store.find_deterministic("revenue")
    assert found_by_tag is not None
    assert found_by_tag.title == "Monthly Churn Rate"

    # Not found
    assert store.find_deterministic("Unknown Concept") is None


def test_hybrid_router_deterministic_precedence(temp_okf_dir: Path) -> None:
    store = OKFStore(temp_okf_dir)
    store.save_document(
        title="Payment Endpoint Contract",
        content="POST /api/v1/payments requires Authorization: Bearer <token>",
        doc_type="api-contract",
        tags=["payments", "api"],
    )

    router = HybridKnowledgeRouter(okf_dir=temp_okf_dir)
    result = router.query("Payment Endpoint Contract")

    assert result["found"] is True
    assert result["source"] == "OKF_CANONICAL"
    assert result["deterministic"] is True
    assert "Authorization: Bearer" in result["content"]


def test_hybrid_router_no_match(temp_okf_dir: Path) -> None:
    router = HybridKnowledgeRouter(okf_dir=temp_okf_dir)
    result = router.query("Nonexistent Query")

    assert result["found"] is False
    assert result["source"] == "NONE"


# ── Invariantes do gate endurecido (0.21.80) ───────────────────────────────
# Reproduzem a classe de bug medida no corpus real: find_deterministic
# aceitava `tag in query` como substring livre, então em queries longas
# uma tag fragmento ('file' ⊂ 'filesystem') ou uma tag isolada ('token')
# interceptava com documento não relacionado, escondendo o RAGFlow que
# tinha o documento certo (teto medido: 0.897@1 / 0.974@3).

def test_okf_gate_fragmento_de_tag_nao_e_evidencia(temp_okf_dir: Path) -> None:
    store = OKFStore(temp_okf_dir)
    store.save_document(
        title="Licao Rust Estatico",
        content="binario musl static-linking",
        doc_type="lesson",
        tags=["rust", "file"],
    )
    # 'file' é fragmento de 'filesystem' — o gate antigo (substring livre)
    # respondia aqui; o endurecido não tem evidência nenhuma.
    assert store.find_deterministic(
        "/tmp fora da RAM e zram como swap nunca como filesystem"
    ) is None


def test_okf_gate_tag_unica_sem_corroboracao_nao_responde(temp_okf_dir: Path) -> None:
    store = OKFStore(temp_okf_dir)
    store.save_document(
        title="Tokens A2A da malha Hermes",
        content="token de 64 chars",
        doc_type="lesson",
        tags=["a2a", "token", "convencao"],
    )
    # 'token' casa como palavra inteira, mas é evidência ÚNICA numa query de
    # 12 palavras sobre outro assunto — o gate antigo respondia por substring.
    assert store.find_deterministic(
        "google drive via rclone refresh token expira em 7 dias consent screen"
    ) is None


def test_okf_gate_duas_tags_palavra_inteira_corroboram(temp_okf_dir: Path) -> None:
    store = OKFStore(temp_okf_dir)
    store.save_document(
        title="Contrato Memoria HAOS",
        content="stack de memoria em runtime",
        doc_type="contract",
        tags=["haos", "memoria", "runtime"],
    )
    # Feature preservada: ≥2 tags distintas do MESMO doc casando como
    # palavras inteiras é evidência corroborada e o gate responde.
    found = store.find_deterministic("qual o contrato de memoria do haos")
    assert found is not None
    assert found.title == "Contrato Memoria HAOS"


def test_okf_gate_tag_hifenizada_exige_sequencia_completa(temp_okf_dir: Path) -> None:
    store = OKFStore(temp_okf_dir)
    store.save_document(
        title="Build estatica",
        content="musl",
        doc_type="lesson",
        tags=["static-linking", "musl"],
    )
    # 'linking' isolado NÃO casa com a tag 'static-linking' (sequência de
    # palavras); e mesmo que casasse, seria evidência única.
    assert store.find_deterministic("o linking dinamico quebrou a build") is None
    # A sequência completa, corroborada por segunda tag, casa.
    found = store.find_deterministic("musl static-linking no binario final")
    assert found is not None
