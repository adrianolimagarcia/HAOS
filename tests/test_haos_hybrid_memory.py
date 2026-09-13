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
