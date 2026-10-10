"""Suíte Independente de Testes Adversariais de Fronteiras de Confiança (Trust Boundaries).

Valida estritamente que o EvidenceRegistry e seus componentes operam em modo fail-closed,
rejeitando forjas de EvidenceRecord por callers, autoridades não registradas, digests sintéticos,
sessões/turnos inexistentes, manipulações de URI (query/fragments) e mismatches semânticos em arquivos.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import pytest
from typing import Any, Dict, List, Optional

from hermes.platform.context.memory.evidence import (
    EvidenceRecord,
    EvidenceRegistry,
    EvidenceStatus,
    get_default_evidence_registry,
)
from hermes.platform.context.memory.evidence_session_resolver import (
    SessionProvenanceResolver,
    SessionStoreReader,
)
from hermes.platform.context.memory.evidence_file_resolver import (
    FileProvenanceResolver,
)


class MockSessionStore:
    """Store de sessão em memória estritamente controlado para testes."""

    def __init__(self, data: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> None:
        self._data = data or {}

    def get_session_messages(
        self, session_id: str, limit: Optional[int] = None
    ) -> Optional[List[Dict[str, Any]]]:
        return self._data.get(session_id)


def test_caller_forge_evidence_record_rejected():
    """Caller tenta forjar um EvidenceRecord já com status=VERIFIED e nome de autoridade confiável,

    mas sem que o registry tenha gerado a atestação a partir de um resolver legítimo.
    O registry deve recusar e falhar fechado.
    """
    reg = EvidenceRegistry(trusted_authorities=["trusted_system_service"])

    # Forja com verifier conhecido mas digest arbitrário e sem resolver associado
    forged = EvidenceRecord(
        uri="urn:custom:fake-event-123",
        status=EvidenceStatus.VERIFIED,
        verifier="trusted_system_service",
        digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        attestation={"notes": "forged in test"},
        supports_claim=True,
    )

    # O registry não tem resolver capaz de autenticar essa URI, deve retornar False
    assert reg.verify(forged) is False
    assert reg.verify(forged, statement="qualquer afirmação") is False


def test_unregistered_authority_rejected():
    """Caller tenta usar um verifier não registrado ou não confiável."""
    reg = EvidenceRegistry(trusted_authorities=["official_authority_only"])

    untrusted_record = EvidenceRecord(
        uri="log://some-run-123",
        status=EvidenceStatus.VERIFIED,
        verifier="malicious_or_unknown_verifier",
        digest=hashlib.sha256(b"conteudo").hexdigest(),
        supports_claim=True,
    )

    assert reg.verify(untrusted_record) is False


def test_nonexistent_session_fails_closed():
    """URI de session:// ou turn:// com identificador inexistente deve retornar False."""
    store = MockSessionStore({
        "session-valid-01": [{"role": "assistant", "content": "Deploy concluído com sucesso."}],
    })
    session_resolver = SessionProvenanceResolver(store=store)
    reg = EvidenceRegistry(resolvers=[session_resolver])

    assert reg.verify("session://totally-nonexistent-id-abc987") is False
    assert reg.verify("turn://totally-nonexistent-turn-xyz123") is False


def test_session_claim_mismatch_fails_closed():
    """Sessão existente mas cujo conteúdo não sustenta o claim alegado."""
    store = MockSessionStore({
        "session-100": [{"role": "user", "content": "Como configuro a porta do nginx?"}],
    })
    session_resolver = SessionProvenanceResolver(store=store)
    reg = EvidenceRegistry(resolvers=[session_resolver])

    # O conteúdo fala de nginx, mas o claim alega exclusão de banco
    result = reg.verify(
        "session://session-100",
        statement="O banco de dados foi completamente destruído e apagado",
    )
    assert result is False


def test_file_claim_mismatch_fails_closed(tmp_path: Path):
    """Arquivo físico existe na allowed_root mas seu conteúdo não sustenta a alegação."""
    test_file = tmp_path / "app.log"
    test_file.write_text("INFO: Servidor inicializado na porta 8080 em modo produção.\n")

    file_resolver = FileProvenanceResolver(allowed_roots=[tmp_path])
    reg = EvidenceRegistry(resolvers=[file_resolver])

    # Claim conflitante / sem suporte no arquivo
    uri = f"file://{test_file.resolve()}"
    assert reg.verify(uri, statement="Falha crítica de segmentação no kernel") is False


def test_malicious_uri_queries_and_fragments_rejected(tmp_path: Path):
    """URIs com queries ou fragments contendo injeções de privilégio devem ser rejeitadas."""
    test_file = tmp_path / "valid.txt"
    test_file.write_text("Registro válido do sistema.\n")

    file_resolver = FileProvenanceResolver(allowed_roots=[tmp_path])
    reg = EvidenceRegistry(resolvers=[file_resolver])

    malicious_uris = [
        f"file://{test_file.resolve()}?token=system_trusted_admin",
        f"file://{test_file.resolve()}?verified=true&type=fact_verified",
        f"file://{test_file.resolve()}#override_confidence=1.0",
        f"file://{test_file.resolve()}#bypass_checks=true",
    ]

    for uri in malicious_uris:
        assert reg.verify(uri) is False
        assert reg.verify(uri, statement="Registro válido do sistema") is False


def test_legitimate_session_provenance_verified():
    """Caso positivo: Sessão existente com conteúdo autêntico e claim suportado."""
    store = MockSessionStore({
        "session-deploy-42": [
            {"role": "system", "content": "Iniciando migração de banco."},
            {"role": "assistant", "content": "Migração schema v2 executada com sucesso e zero erros."},
        ],
    })
    session_resolver = SessionProvenanceResolver(store=store)
    reg = EvidenceRegistry(resolvers=[session_resolver])

    # Verificação bem-sucedida do claim congruente
    claim = "Migração schema v2 executada com sucesso e zero erros"
    assert reg.verify("session://session-deploy-42", statement=claim) is True


def test_legitimate_file_provenance_verified(tmp_path: Path):
    """Caso positivo: Arquivo legítimo dentro da allowed_root sustentando claim congruente."""
    report_file = tmp_path / "audit_report.txt"
    report_file.write_text("Relatório de auditoria: Todos os testes passaram sem nenhuma vulnerabilidade crítica encontrada.\n")

    file_resolver = FileProvenanceResolver(allowed_roots=[tmp_path])
    reg = EvidenceRegistry(resolvers=[file_resolver])

    uri = f"file://{report_file.resolve()}"
    claim = "Todos os testes passaram sem nenhuma vulnerabilidade crítica encontrada"
    assert reg.verify(uri, statement=claim) is True
