"""Independent challenge cases: do not use the frozen training/acceptance fixture."""
import pytest
from hermes.platform.context.memory.classifier import classify_assertion, AssertionType

@pytest.mark.parametrize("statement,evidence_refs", [
    ("O serviço está saudável", ["log://inventado"]),
    ("O banco está indisponível", ["https://example.invalid/proof"]),
    ("O erro foi causado pelo DNS", ["file:///tmp/nao-existe"]),
    ("O gerente afirmou que houve falha", ["log://inventado"]),
    ("Observed: servidor com CPU 90%", ["cmd://nao-executado"]),
    ("The service is healthy", ["https://example.invalid/verified"]),
    ("The report said that the database is down", ["urn:unverified:123"]),
])
def test_fake_reference_is_not_proof(statement, evidence_refs):
    c = classify_assertion(statement, evidence_refs=evidence_refs)
    assert c.assertion_type != AssertionType.FACT_VERIFIED, (statement, c.rationale_codes)

@pytest.mark.parametrize("statement", [
    "Talvez o banco esteja fora", "Possivelmente timeout no serviço",
    "Maybe the service is down", "I suspect the connection was dropped",
])
def test_explicit_hypotheses_are_blocked(statement):
    assert classify_assertion(statement).assertion_type == AssertionType.HYPOTHESIS
