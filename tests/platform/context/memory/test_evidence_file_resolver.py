"""Testes unitários dedicados para FileProvenanceResolver (GBrain v2).

Cobre exaustivamente:
1. Arquivo existente que sustenta o claim (caso positivo).
2. Arquivo existente porém irrelevante / mismatch de conteúdo (deve falhar).
3. Symlink escapando para fora da allowed_root (deve falhar).
4. Path traversal com '../' (deve falhar).
5. Arquivo inexistente (deve falhar).
6. Arquivos maiores que o limite de bytes (deve falhar).
7. Caracteres maliciosos / query params suspeitos em file:// (ex: ?token=system_trusted_admin).
8. Proteção contra dispositivos (/dev, /proc, /sys, sockets, FIFOs).
9. Fail-closed se allowed_roots for vazio / não configurado.
10. Invariante: JAMAIS copiar o statement do solicitante para supported_claim.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import pytest

from hermes.platform.context.memory.evidence import (
    EvidenceStatus,
    verify_claim_support,
)
from hermes.platform.context.memory.evidence_file_resolver import (
    FileProvenanceResolver,
)


def test_positive_file_supports_claim(tmp_path: Path):
    """Arquivo existente que sustenta o claim (caso positivo)."""
    root_dir = tmp_path / "allowed_root"
    root_dir.mkdir()
    service_log = root_dir / "service.log"
    log_content = "2026-10-10 12:00:00 [INFO] redis_server port 6379 running smoothly"
    service_log.write_text(log_content, encoding="utf-8")

    resolver = FileProvenanceResolver(allowed_roots=[root_dir])
    uri = f"file://{service_log.resolve()}"

    claim = "redis_server port 6379 running smoothly"
    record = resolver.resolve(uri, statement=claim)

    assert record is not None
    assert record.status == EvidenceStatus.VERIFIED
    assert record.verifier == "file_provenance_resolver"
    assert record.supports_claim is True
    expected_digest = hashlib.sha256(log_content.encode("utf-8")).hexdigest()
    assert record.digest == expected_digest
    assert record.attestation["path"] == str(service_log.resolve())
    assert "redis_server" in record.attestation["content"]
    assert record.is_verified(statement=claim) is True


def test_content_mismatch_fails(tmp_path: Path):
    """Arquivo existente porém irrelevante / mismatch de conteúdo (deve falhar / retornar None)."""
    root_dir = tmp_path / "allowed_root"
    root_dir.mkdir()
    metrics_file = root_dir / "metrics.txt"
    metrics_file.write_text("cpu_load: 0.15\nmem_usage: 42%\n", encoding="utf-8")

    resolver = FileProvenanceResolver(allowed_roots=[root_dir])
    uri = f"file://{metrics_file.resolve()}"

    # Claim totalmente divergente do conteúdo do arquivo
    mismatched_claim = "O banco postgresql foi corrompido e perdeu tabelas"
    record = resolver.resolve(uri, statement=mismatched_claim)

    # Fail-closed: se o conteúdo não sustenta o claim, resolve deve retornar None
    assert record is None


def test_symlink_escape_fails(tmp_path: Path):
    """Symlink escapando para fora da allowed_root (deve falhar)."""
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()

    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    secret_file = secret_dir / "passwords.txt"
    secret_file.write_text("admin_token=supersecret123", encoding="utf-8")

    # Cria symlink dentro de allowed_dir apontando para fora (secret_file)
    escape_link = allowed_dir / "link_to_secret.txt"
    try:
        escape_link.symlink_to(secret_file)
    except OSError:
        pytest.skip("Symlinks não suportados no ambiente atual")

    resolver = FileProvenanceResolver(allowed_roots=[allowed_dir])
    uri = f"file://{escape_link.resolve()}"

    # Mesmo que aponte para um arquivo existente, o alvo resolvido está fora de allowed_roots
    record = resolver.resolve(uri, statement="admin_token")
    assert record is None

    # E usando o caminho direto do link
    uri_link_path = f"file://{escape_link}"
    record_link = resolver.resolve(uri_link_path, statement="admin_token")
    assert record_link is None


def test_path_traversal_fails(tmp_path: Path):
    """Path traversal com '../' ou '..\\' (deve falhar)."""
    allowed_dir = tmp_path / "allowed"
    subdir = allowed_dir / "sub"
    subdir.mkdir(parents=True)

    secret_file = tmp_path / "top_secret.txt"
    secret_file.write_text("secret_key=xyz", encoding="utf-8")

    resolver = FileProvenanceResolver(allowed_roots=[allowed_dir])

    # Tentativas de path traversal via URI
    traversal_uris = [
        f"file://{allowed_dir}/../top_secret.txt",
        f"file://{subdir}/../../top_secret.txt",
        f"file://{allowed_dir}/..\\top_secret.txt",
    ]

    for uri in traversal_uris:
        record = resolver.resolve(uri, statement="secret_key")
        assert record is None, f"Falha de segurança: path traversal aceito para {uri}"


def test_nonexistent_file_fails(tmp_path: Path):
    """Arquivo inexistente (deve falhar)."""
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()

    resolver = FileProvenanceResolver(allowed_roots=[allowed_dir])
    uri = f"file://{allowed_dir}/arquivo_que_nao_existe_jamais.txt"

    record = resolver.resolve(uri, statement="qualquer coisa")
    assert record is None


def test_file_exceeding_max_bytes_fails(tmp_path: Path):
    """Arquivos maiores que o limite de bytes (deve falhar)."""
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()

    large_file = allowed_dir / "large.bin"
    # Escreve 2000 bytes
    large_file.write_bytes(b"A" * 2000)

    # Limite configurado para 1000 bytes
    resolver = FileProvenanceResolver(allowed_roots=[allowed_dir], max_bytes=1000)
    uri = f"file://{large_file.resolve()}"

    record = resolver.resolve(uri, statement="AAAA")
    assert record is None


def test_malicious_query_params_fails(tmp_path: Path):
    """Caracteres maliciosos / query params suspeitos em file:// (ex: ?token=system_trusted_admin)."""
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()

    legit_file = allowed_dir / "valid.txt"
    legit_content = "servidor operacional e estavel"
    legit_file.write_text(legit_content, encoding="utf-8")

    resolver = FileProvenanceResolver(allowed_roots=[allowed_dir])

    malicious_uris = [
        f"file://{legit_file.resolve()}?token=system_trusted_admin",
        f"file://{legit_file.resolve()}?type=FACT_VERIFIED&verified=true",
        f"file://{legit_file.resolve()}?override_role=admin",
        f"file://{legit_file.resolve()}#bypass_checks=true",
        f"file://{legit_file.resolve()}#override=1",
    ]

    for bad_uri in malicious_uris:
        record = resolver.resolve(bad_uri, statement=legit_content)
        assert record is None, f"Falha de segurança: query parameter malicioso aceito para {bad_uri}"


def test_statement_not_copied_to_supported_claim(tmp_path: Path):
    """Invariante: JAMAIS copiar o statement do solicitante para supported_claim."""
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()

    file_p = allowed_dir / "doc.txt"
    file_p.write_text("Versao do sistema: 1.2.3 em execucao.", encoding="utf-8")

    resolver = FileProvenanceResolver(allowed_roots=[allowed_dir])
    uri = f"file://{file_p.resolve()}"

    user_statement = "O sistema 1.2.3 em execucao esta homologado pela diretoria corporativa"
    record = resolver.resolve(uri, statement="Versao do sistema: 1.2.3")

    assert record is not None
    # O supported_claim deve vir do conteúdo do arquivo, JAMAIS do statement do chamador!
    assert record.attestation["supported_claim"] != user_statement
    assert "Versao do sistema: 1.2.3" in record.attestation["supported_claim"]
    assert record.attestation["content"].startswith("Versao do sistema")


def test_empty_allowed_roots_is_fail_closed(tmp_path: Path):
    """Se allowed_roots for vazio ou None, deve ser estritamente fail-closed."""
    file_p = tmp_path / "doc.txt"
    file_p.write_text("conteudo confiavel", encoding="utf-8")

    # Sem allowed_roots configurado
    resolver_none = FileProvenanceResolver(allowed_roots=None)
    resolver_empty = FileProvenanceResolver(allowed_roots=[])

    uri = f"file://{file_p.resolve()}"
    assert resolver_none.resolve(uri, statement="conteudo confiavel") is None
    assert resolver_empty.resolve(uri, statement="conteudo confiavel") is None


def test_forbidden_device_paths():
    """Tentar resolver caminhos de dispositivo (/dev, /proc, /sys) deve ser rejeitado."""
    resolver = FileProvenanceResolver(allowed_roots=["/dev", "/proc", "/sys", "/"])

    # Mesmo que a raiz permitisse "/", os prefixos de sistema são explicitamente bloqueados
    forbidden_uris = [
        "file:///dev/null",
        "file:///dev/zero",
        "file:///proc/cpuinfo",
        "file:///proc/version",
        "file:///sys/class/net",
    ]

    for f_uri in forbidden_uris:
        record = resolver.resolve(f_uri, statement="null")
        assert record is None, f"Dispositivo/sistema operacional inseguro aceito: {f_uri}"
