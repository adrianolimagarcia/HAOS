"""Behavior contracts for the typed contextual assertion classifier."""

from dataclasses import fields

import pytest

from hermes.platform.context.memory.classifier import (
    AssertionClassification,
    AssertionType,
    classify_assertion,
)
from hermes.platform.context.memory.candidate import contains_hypothesis


@pytest.mark.parametrize("text", [
    "Talvez o banco esteja lento.", "Provavelmente há um erro.",
    "Possivelmente o cache expirou.", "Esta é uma hipótese.",
    "Maybe the database is slow.", "Perhaps the cache expired.",
    "Probably there is an error.", "This is a hypothesis.",
])
def test_explicit_uncertainty_is_hypothesis(text):
    assert classify_assertion(text).assertion_type is AssertionType.HYPOTHESIS
    assert contains_hypothesis(text)


@pytest.mark.parametrize("text", [
    "Deve ser um problema na rede.", "Parece que o serviço caiu.",
    "Aparentemente o processo encerrou.", "Suspeita-se de falha no disco.",
    "It seems that the service stopped.", "It appears to be a network issue.",
    "Presumably the process ended.",
])
def test_implicit_uncertainty_is_hypothesis(text):
    assert classify_assertion(text).assertion_type is AssertionType.HYPOTHESIS


def test_reported_quote_with_uncertainty_classifies_attribution():
    result = classify_assertion('O relatório disse que: “talvez o serviço caiu”.')
    assert result.assertion_type is AssertionType.FACT_UNVERIFIED
    assert result.is_cited
    assert "CITATION_ATTRIBUTION" in result.rationale_codes


def test_quote_with_uncertainty_does_not_mask_uncertain_attribution():
    result = classify_assertion('Talvez, segundo o relatório, o serviço caiu.')
    assert result.assertion_type is AssertionType.HYPOTHESIS
    assert result.is_cited


@pytest.mark.parametrize("text", [
    "OBSERVED: service started", "exit code 0: tests passed", "verificado nos logs: serviço ativo"
])
def test_observed_text_with_evidence_is_verified(text):
    result = classify_assertion(text, ["log://run/1"])
    assert result.assertion_type is AssertionType.FACT_VERIFIED
    assert "VERIFIED_OBSERVATION" in result.rationale_codes


def test_evidence_refs_verify_unmodalized_fact():
    result = classify_assertion("O servidor usa a porta 8080.", ["file://config"])
    assert result.assertion_type is AssertionType.FACT_VERIFIED


def test_evidence_does_not_override_uncertainty():
    result = classify_assertion("Talvez o servidor use a porta 8080.", ["file://config"])
    assert result.assertion_type is AssertionType.HYPOTHESIS


@pytest.mark.parametrize("text", [
    "A hipótese foi refutada.", "Ficou comprovado que não era um bug.",
    "The claim was refuted.", "The claim was disproven.",
])
def test_refutation_marker_wins(text):
    assert classify_assertion(text).assertion_type is AssertionType.REFUTED


def test_factual_fallback_is_unverified_not_verified():
    result = classify_assertion("O servidor usa a porta 8080.")
    assert result.assertion_type is AssertionType.FACT_UNVERIFIED
    assert "UNVERIFIED_FALLBACK" in result.rationale_codes


def test_metadata_contract_and_defaults():
    result = classify_assertion("Claim", ["urn:evidence"], "unit-test")
    assert isinstance(result, AssertionClassification)
    assert 0 <= result.confidence <= 1
    assert result.evidence_refs == ["urn:evidence"]
    assert result.source_provenance == "unit-test"
    assert result.assertion_id
    assert result.refuted_assertion_id is None
    assert result.observed_at > 0
    assert result.valid_from is result.valid_until is None
    assert result.raw_text == "Claim"
    assert result.is_cited is False
    assert [f.name for f in fields(result)] == [
        "assertion_type", "confidence", "rationale_codes", "evidence_refs",
        "source_provenance", "assertion_id", "refuted_assertion_id", "observed_at",
        "valid_from", "valid_until", "raw_text", "is_cited",
    ]
