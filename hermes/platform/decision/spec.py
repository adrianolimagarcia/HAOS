"""Tipos e especificações do motor de decisões tipadas System One do HAOS.

Implementa o paradigma de decisões estruturadas (Choice, Boolean, Score)
com aprendizado adaptativo e calibração de confiança.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import time


@dataclass
class DecisionChoice:
    selected: str
    confidence: float
    probabilities: Dict[str, float] = field(default_factory=dict)
    reason: str = ""
    learned: bool = False
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionBoolean:
    verdict: bool
    confidence: float
    reason: str = ""
    category: str = "general"
    learned: bool = False
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionScore:
    score: float
    confidence: float
    reason: str = ""
    learned: bool = False
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionRecord:
    pattern_key: str
    domain: str
    raw_signature: str
    decision_type: str  # choice, boolean, score
    result_json: str
    confidence: float
    hit_count: int = 1
    created_at: float = field(default_factory=time.time)
    last_hit_at: float = field(default_factory=time.time)
