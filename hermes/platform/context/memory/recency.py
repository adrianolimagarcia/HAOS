"""Deterministic recency decay for Memory Fabric retrieval.

Absorvido do CAMEL (``ChatHistoryBlock`` multiplica o score por ``keep_rate**k``
a cada passo atrás no histórico; blocos SYSTEM ficam sempre em 1.0). Aqui o
conceito vira uma função pura com três promessas:

* ``final = relevance * keep_rate ** age_days`` — meia-vida ~14 dias com o
  default 0.95 (``0.95**14 ≈ 0.488``), então memória velha compete por
  relevância, não por antiguidade.
* Records fundacionais pinados (``metadata['pin']``/``metadata['pinned']`` ou
  ``kind == 'system'``) têm peso 1.0: decaimento nenhum os apaga do recall.
* Determinismo: a função pura recebe ``age_days`` pronto; o ``now`` é injetável
  no call-site. Nada dentro de ``apply_recency`` lê relógio de parede.

Config: ``HAOS_MEMORY_RECENCY_KEEP_RATE`` (float, default 0.95, range (0, 1];
``1.0`` desliga o decay). Valor inválido cai no default com warning — retrieval
não pode quebrar a turn por causa de um typo de operador.
"""
from __future__ import annotations

import logging
import math
import os
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Default keep-rate: 0.95 por dia ≈ meia-vida de 14 dias.
DEFAULT_KEEP_RATE = 0.95

#: Variável de ambiente do operador (0, 1]; 1.0 desliga o decaimento.
ENV_KEEP_RATE = "HAOS_MEMORY_RECENCY_KEEP_RATE"

_SECONDS_PER_DAY = 86400.0


def keep_rate_from_env(raw: Optional[str] = None) -> float:
    """Resolve the operator's keep-rate: env override, else the default.

    ``raw`` (when given) replaces the environment lookup entirely, which keeps
    this branch testable without monkeypatching. Anything outside ``(0, 1]`` —
    including a non-numeric typo — falls back to ``DEFAULT_KEEP_RATE`` with a
    warning: a misconfigured decay must degrade to sane behaviour, never to a
    broken retrieval path.
    """
    if raw is None:
        raw = os.getenv(ENV_KEEP_RATE)
    if raw is None:
        return DEFAULT_KEEP_RATE
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r is not a float; using default %s", ENV_KEEP_RATE, raw, DEFAULT_KEEP_RATE)
        return DEFAULT_KEEP_RATE
    if not (0.0 < value <= 1.0) or math.isnan(value):
        logger.warning("%s=%r is outside (0, 1]; using default %s", ENV_KEEP_RATE, raw, DEFAULT_KEEP_RATE)
        return DEFAULT_KEEP_RATE
    return value


def age_days(valid_from: float, now: Optional[float] = None) -> float:
    """Age of a record in days, clamped at 0 (future timestamps never inflate).

    ``now`` defaults to wall-clock at the call-site — outside the pure function —
    so tests inject a fixed instant and ranking stays reproducible.
    """
    reference = now if now is not None else time.time()
    age = (reference - float(valid_from)) / _SECONDS_PER_DAY
    return age if age > 0.0 else 0.0


def is_pinned(record: Any) -> bool:
    """True for foundational records protected from decay (CAMEL's SYSTEM rule).

    Reads the real ``MemoryRecord`` surface: ``metadata['pin']`` /
    ``metadata['pinned']`` (default False when absent) or ``kind == 'system'``.
    """
    metadata = getattr(record, "metadata", None)
    if isinstance(metadata, dict) and (metadata.get("pin") is True or metadata.get("pinned") is True):
        return True
    return getattr(record, "kind", None) == "system"


def apply_recency(relevance: float, age_days: float, keep_rate: float, pinned: bool = False) -> float:
    """``relevance * keep_rate ** age_days`` with pin protection — pure, no clock.

    Raises ``ValueError`` for a keep_rate outside ``(0, 1]``: the env path
    degrades gracefully, but a programmatic caller passing 0/negative/>1 is a
    bug and must not silently distort every score.
    """
    if math.isnan(keep_rate) or not (0.0 < keep_rate <= 1.0):
        raise ValueError("keep_rate must be in (0, 1], got %r" % (keep_rate,))
    if pinned or keep_rate == 1.0:
        return relevance
    age = age_days if age_days > 0.0 else 0.0
    return relevance * (keep_rate ** age)
