"""Módulo de resolução segura de proveniência de arquivos (File Provenance Resolver).

Garante que referências 'file://' sejam estritamente validadas contra raízes autorizadas,
prevenindo path traversal, symlink escapes fora de limites, dispositivos especiais (FIFOs, sockets,
pipes, dev, proc, sys) e arquivos que excedam limites de tamanho.
Além disso, calcula SHA-256 do conteúdo real e avalia congruência semântica fail-closed
sem copiar cegamente claims do solicitante.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Any, Dict, Iterable, List, Optional, Set, Union
import urllib.parse

from hermes.platform.context.memory.evidence import (
    EvidenceRecord,
    EvidenceStatus,
    _SUSPICIOUS_URI_PATTERNS,
    _extract_keywords,
    verify_claim_support,
)

# Diretórios de sistema proibidos
_FORBIDDEN_PATH_PREFIXES = (
    "/dev",
    "/proc",
    "/sys",
)

# Padrões suspeitos em query strings / fragmentos
_SUSPICIOUS_QUERY_FRAGMENT_PATTERNS = re.compile(
    r"(?:token|admin|trusted|role|auth|override|bypass|system_trusted|verified=true|type=fact_verified)",
    re.IGNORECASE,
)


class FileProvenanceResolver:
    """Resolver confiável e seguro para arquivos locais existentes no filesystem (fail-closed)."""

    name = "file_provenance_resolver"

    def __init__(
        self,
        allowed_roots: Optional[Iterable[Union[str, Path]]] = None,
        max_bytes: int = 1024 * 1024,  # Limite padrão: 1 MB
    ) -> None:
        """Inicializa o FileProvenanceResolver.

        Args:
            allowed_roots: Coleção de caminhos raiz permitidos. Se None ou vazio,
                           nenhum arquivo será resolvido por padrão (fail-closed seguro).
            max_bytes: Tamanho máximo permitido em bytes para leitura do arquivo.
        """
        self.max_bytes = max_bytes
        self.allowed_roots: List[Path] = []
        if allowed_roots:
            for root in allowed_roots:
                try:
                    p = Path(root).resolve()
                    self.allowed_roots.append(p)
                except Exception:
                    continue

    def can_resolve(self, uri: str) -> bool:
        """Verifica se a URI é potencialmente tratável por este resolver."""
        if not isinstance(uri, str):
            return False
        u = uri.strip()
        if not u.startswith("file://"):
            return False
        if _SUSPICIOUS_URI_PATTERNS.search(u):
            return False
        return True

    def _parse_and_validate_uri(self, uri: str) -> Optional[Path]:
        """Faz parse de file:// URI e valida ausência de query/fragment maliciosos."""
        try:
            parsed = urllib.parse.urlparse(uri.strip())
            if parsed.scheme != "file":
                return None

            # Rejeita query params ou fragmentos suspeitos que tentem manipular segurança
            if parsed.query:
                if _SUSPICIOUS_QUERY_FRAGMENT_PATTERNS.search(parsed.query):
                    return None
            if parsed.fragment:
                if _SUSPICIOUS_QUERY_FRAGMENT_PATTERNS.search(parsed.fragment):
                    return None

            raw_path = urllib.parse.unquote(parsed.path)
            if not raw_path:
                return None

            # Rejeita strings com traversal explícito antes de qualquer resolução
            if ".." in raw_path.replace("\\", "/").split("/"):
                return None

            return Path(raw_path)
        except Exception:
            return None

    def _is_path_allowed(self, target_path: Path) -> bool:
        """Verifica se target_path resolve rigorosamente dentro de uma allowed_root."""
        if not self.allowed_roots:
            return False

        try:
            # Resolve symlinks reais e normaliza caminho absoluto
            resolved_target = target_path.resolve(strict=True)
        except (FileNotFoundError, RuntimeError, OSError):
            return False

        # Verifica restrição de prefixos de sistema perigosos
        resolved_str = str(resolved_target)
        for forbidden in _FORBIDDEN_PATH_PREFIXES:
            if resolved_str == forbidden or resolved_str.startswith(forbidden + "/"):
                return False

        # Verifica se o arquivo resolvido reside dentro de alguma allowed_root autorizada
        for root in self.allowed_roots:
            try:
                # relative_to lança ValueError se resolved_target não estiver sob root
                resolved_target.relative_to(root)
                return True
            except ValueError:
                continue

        return False

    def _validate_regular_file(self, resolved_path: Path) -> bool:
        """Valida que o caminho é estritamente um arquivo regular e não dispositivo/pipe/socket."""
        try:
            st = resolved_path.stat()
            # Deve ser arquivo regular
            if not stat.S_ISREG(st.st_mode):
                return False
            # Rejeita FIFOs, character/block devices, sockets
            if (
                stat.S_ISFIFO(st.st_mode)
                or stat.S_ISCHR(st.st_mode)
                or stat.S_ISBLK(st.st_mode)
                or stat.S_ISSOCK(st.st_mode)
            ):
                return False
            # Rejeita se o tamanho em disco já excede o limite seguro
            if st.st_size > self.max_bytes:
                return False
            # Verifica permissão de leitura
            if not os.access(resolved_path, os.R_OK):
                return False
            return True
        except Exception:
            return False

    def _read_and_digest(self, resolved_path: Path) -> Optional[tuple[str, str]]:
        """Lê os bytes reais do arquivo de forma segura, calculando o SHA-256."""
        hasher = hashlib.sha256()
        total_read = 0
        chunks: List[bytes] = []

        try:
            with open(resolved_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    total_read += len(chunk)
                    if total_read > self.max_bytes:
                        return None
                    hasher.update(chunk)
                    if len(chunks) * 65536 < 4096:
                        chunks.append(chunk)

            digest = hasher.hexdigest()
            raw_sample = b"".join(chunks)[:4096]
            sample_text = raw_sample.decode("utf-8", errors="replace")
            return digest, sample_text
        except Exception:
            return None

    def _extract_supported_claims(self, text_sample: str) -> List[str]:
        """Extrai afirmações factuais derivadas do conteúdo real do arquivo."""
        lines = [line.strip() for line in text_sample.splitlines() if line.strip()]
        # Retorna primeiras linhas não vazias como amostras de claims suportados
        return lines[:10]

    def resolve(self, uri: str, statement: str = "") -> Optional[EvidenceRecord]:
        """Resolve a referência de arquivo local garantindo todas as travas de segurança.

        Args:
            uri: URI do arquivo (file://...)
            statement: Afirmação a ser suportada (opcional, mas obrigatória para verificação de claim)

        Returns:
            EvidenceRecord se for válido e seguro; None caso contrário.
        """
        if not self.can_resolve(uri):
            return None

        target_path = self._parse_and_validate_uri(uri)
        if target_path is None:
            return None

        # Valida restrição de raiz e prevenção de traversal/symlink escape
        if not self._is_path_allowed(target_path):
            return None

        resolved_path = target_path.resolve()

        # Valida tipo de arquivo (regular), permissão e tamanho seguro
        if not self._validate_regular_file(resolved_path):
            return None

        # Lê bytes e calcula digest
        res = self._read_and_digest(resolved_path)
        if res is None:
            return None

        digest, sample_text = res

        # Deriva atestação e claims suportados baseados exclusivamente no conteúdo lido
        supported_claims = self._extract_supported_claims(sample_text)

        # JAMAIS copiar o statement do solicitante para supported_claim!
        # O supported_claim deve ser derivado do conteúdo real do arquivo.
        record = EvidenceRecord(
            uri=uri.strip(),
            status=EvidenceStatus.VERIFIED,
            verifier=self.name,
            digest=digest,
            attestation={
                "path": str(resolved_path),
                "content": sample_text,
                "supported_claims": supported_claims,
                # supported_claim derivado da primeira linha de conteúdo real
                "supported_claim": supported_claims[0] if supported_claims else "",
            },
            supports_claim=True,
        )

        # Se statement foi fornecido, avalia congruência semântica fail-closed
        if statement and statement.strip():
            if not verify_claim_support(record, statement):
                return None

        return record
