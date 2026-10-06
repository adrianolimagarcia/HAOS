"""Testes de conformidade e integridade para o JevOptionScorer, Fast-Gated Inference e Batch Training."""

from pathlib import Path
import tempfile
import pytest
import numpy as np

from hermes.platform.decision.jev_scorer import JevOptionScorer
from hermes.platform.decision.spec import DecisionChoice
from hermes.platform.decision.engine import SystemOneEngine
from hermes.platform.decision.store import DecisionStore


def test_jev_scorer_initialization():
    scorer = JevOptionScorer(width=64, rank=32)
    assert scorer.width == 64
    assert scorer.rank == 32
    assert scorer.w_query.shape == (64, 32)
    assert scorer.w_key.shape == (64, 32)
    assert scorer.w_value.shape == (64, 32)


def test_jev_scorer_decide_basic():
    scorer = JevOptionScorer(width=64, rank=32)
    context = "User wants to disassemble an Android APK binary"
    options = ["run_apktool", "search_web_news", "delete_file"]

    decision = scorer.decide(context, options)

    assert isinstance(decision, DecisionChoice)
    assert decision.selected in options
    assert 0.0 <= decision.confidence <= 1.0
    assert len(decision.probabilities) == 3
    total_prob = sum(decision.probabilities.values())
    assert pytest.approx(total_prob, 0.01) == 1.0
    assert decision.latency_ms >= 0.0


def test_jev_scorer_single_option():
    scorer = JevOptionScorer()
    single = scorer.decide("Context", ["only_one"])
    assert single.selected == "only_one"
    assert single.confidence == 1.0


def test_jev_scorer_training_and_weights_persistence():
    scorer = JevOptionScorer(width=64, rank=32, seed=123)
    context = "Security audit found exposed private key in code"
    options = ["revoke_and_rotate_key", "ignore_alert", "print_joke"]
    target_idx = 0  # Opção correta é a primeira

    # Teste de um passo analítico
    initial_loss = scorer.train_step(context, options, target_idx, lr=0.05)
    assert initial_loss > 0.0

    # Teste de treino em lote (5 épocas)
    samples = [
        (context, options, target_idx),
        ("Decompile executable ELF", ["ghidra_headless", "write_poem"], 0),
    ]
    final_loss = scorer.train_batch(samples, epochs=10, lr=0.05)
    assert final_loss < initial_loss

    # Teste de persistência de pesos
    with tempfile.TemporaryDirectory() as tmpdir:
        w_path = Path(tmpdir) / "test_weights.npz"
        scorer.save_weights(w_path)
        assert w_path.exists()

        loaded_scorer = JevOptionScorer.load_weights(w_path)
        assert np.allclose(scorer.w_query, loaded_scorer.w_query)
        assert np.allclose(scorer.w_key, loaded_scorer.w_key)
        assert np.allclose(scorer.w_value, loaded_scorer.w_value)


def test_system_one_fast_gated_inference(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_decisions.db"
        store = DecisionStore(db_path=db_path)
        scorer = JevOptionScorer(seed=42)

        # 1. Com threshold baixo (ex: 0.20), o fast-gated aceita direto o Jev sem chamar o LLM
        engine_fast = SystemOneEngine(store=store, scorer=scorer, confidence_threshold=0.20)
        res = engine_fast.decide_choice(
            question="What tool to use?",
            options=["tool_x", "tool_y"],
            context="Testing fast gate",
        )
        assert "Fast-gated Jev decision" in res.reason

        # 2. Com threshold alto (ex: 0.99), o Jev não atinge a confiança necessária e escala para o LLM
        engine_escalate = SystemOneEngine(store=store, scorer=scorer, confidence_threshold=0.99)
        # Mock do LLM local
        monkeypatch.setattr(
            engine_escalate,
            "_call_gemini_lite",
            lambda prompt, schema: {
                "selected": "tool_x",
                "confidence": 0.96,
                "probabilities": {"tool_x": 0.96, "tool_y": 0.04},
                "reason": "Escalated LLM decision",
            },
        )
        res_llm = engine_escalate.decide_choice(
            question="What tool to use next?",
            options=["tool_x", "tool_y"],
            context="Testing escalation",
        )
        assert res_llm.selected == "tool_x"
        assert res_llm.reason == "Escalated LLM decision"

        # 3. Amostra gravada no store deve estar disponível para treino offline
        training_samples = store.get_choice_training_samples()
        assert len(training_samples) >= 1
        assert training_samples[0][1] == ["tool_x", "tool_y"]

        # 4. Executa treino offline a partir do store
        train_res = engine_escalate.train_jev_offline(epochs=2, lr=0.01)
        assert train_res["status"] == "trained"
        assert train_res["samples_count"] >= 1
