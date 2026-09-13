"""Research fetcher de PRODUÇÃO out-of-process (forma B do INTEGRATIONS;
COMPLIANCE delta 39).

O padrão DeerFlow absorvido (Fase 1) injeta um ``fetcher`` *callable seam*
síncrono/awaitable no ResearchProvider. O fetcher real de produção não roda
em processo (nunca rede em processo por padrão): esta camada executa um
PEER de subprocesso (script/serviço) que fala um contrato canônico por
stdout JSON. O worker scriptado segue o estilo LSP/ANP: emissor de uma
linha JSON por resposta no stdout, log em stderr; rc != 0 sem linha ->
erro. Sem peer configurado => available() False e acquire levanta
(fail-closed).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from hermes.platform.capabilities.registry import (
    Capability, CapabilityProvider, CapabilityRegistry,
)
from hermes.platform.capabilities.research.worker import (
    ResearchProvider, register_research_provider,
)


class SubprocessFetcherError(RuntimeError):
    pass


class SubprocessFetcher:
    """Fetcher canônico {url,title,content,score,...} via peer subprocesso.

    Peer = executável (CLI de pesquisa/serviço de fetch). Protocolo:
    ``<peer> <question>`` -> stdout: exatamente uma linha JSON com
    ``{"results": [...]}`` (array de dicts canônicos). rc != 0 sem a linha
    -> SubprocessFetcherError (fail-closed).
    """

    def __init__(self, peer_command: str, *,
                 timeout_seconds: float = 60.0,
                 max_results: int = 10):
        self.peer_command = peer_command
        self.timeout_seconds = timeout_seconds
        self.max_results = max_results

    def available(self) -> bool:
        return bool(self.peer_command and shutil.which(self.peer_command))

    def __call__(self, question: str) -> List[Dict[str, Any]]:
        if not self.available():
            raise SubprocessFetcherError(
                f"research fetcher peer {self.peer_command!r} not available "
                f"(fail-closed)")
        try:
            proc = subprocess.run(
                [self.peer_command, question],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise SubprocessFetcherError(
                f"research fetcher peer timed out after "
                f"{self.timeout_seconds}s") from exc
        if proc.returncode != 0:
            raise SubprocessFetcherError(
                f"research fetcher peer exited rc {proc.returncode}: "
                f"{proc.stderr.strip()[:300]}")
        line = (proc.stdout or "").strip()
        if not line:
            raise SubprocessFetcherError(
                "research fetcher peer returned no JSON line (fail-closed)")
        try:
            payload = json.loads(line.splitlines()[-1])
        except (ValueError, UnicodeDecodeError) as exc:
            raise SubprocessFetcherError(
                f"research fetcher peer returned invalid JSON: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(
                payload.get("results"), list):
            raise SubprocessFetcherError(
                "research fetcher peer payload must be {'results': [...]}")
        return [r for r in payload["results"]
                if isinstance(r, dict)][: self.max_results]


def make_research_provider(
    fetcher: Optional[Callable[[str], Any]] = None,
    *,
    peer_command: Optional[str] = None,
    timeout_seconds: float = 60.0,
    max_results: int = 10,
    **kwargs: Any,
) -> ResearchProvider:
    """Provider de pesquisa com fetcher out-of-process (peer de subprocesso).

    Precedência: fetcher explícito (testes/demo) > peer_command (produção).
    Sem nenhum -> provider fail-closed (probe unavailable).
    """
    if fetcher is None and peer_command:
        fetcher = SubprocessFetcher(
            peer_command, timeout_seconds=timeout_seconds, max_results=max_results,
        )
    return ResearchProvider(fetcher=fetcher, **kwargs)


def register_research_fetcher_provider(
    registry: CapabilityRegistry,
    *,
    peer_command: Optional[str] = None,
    fetcher: Optional[Callable[[str], Any]] = None,
    **kwargs: Any,
) -> ResearchProvider:
    provider = make_research_provider(fetcher, peer_command=peer_command, **kwargs)
    register_research_provider(registry, provider)
    return provider
