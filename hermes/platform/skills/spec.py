"""SkillSpec & Procedural Intelligence Lifecycle (K2 / Etapa 2).

Transforma skills em componentes formais:
- SkillSpec: Especificação tipada com dependências, capabilities exigidas,
  modelos/posturas preferidas e hash de proveniência para supply-chain security.
- Lifecycle: candidate -> sandbox -> eval -> active -> deprecated.
- Validação estrita de segurança e integridade de supply-chain.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

SkillLifecycleStatus = Literal["candidate", "sandbox", "eval", "active", "deprecated"]


@dataclass
class SkillSpec:
    """Especificação formal de uma Skill como unidade de inteligência procedural."""

    name: str
    description: str
    version: str = "1.0.0"
    author: str = "Adriano + HAOS"
    license: str = "MIT"
    status: SkillLifecycleStatus = "candidate"
    capabilities_required: List[str] = field(default_factory=list)
    preferred_posture: str = "coder"
    preferred_model_family: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)  # ex.: ["pip:requests", "cli:git"]
    entry_script: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    eval_score: Optional[float] = None
    checksum_sha256: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def calculate_checksum(self, skill_body: str) -> str:
        """Gera assinatura SHA-256 do conteúdo para proteção contra supply-chain tampering."""
        raw = f"{self.name}:{self.version}:{skill_body}".encode("utf-8")
        self.checksum_sha256 = hashlib.sha256(raw).hexdigest()
        return self.checksum_sha256

    def promote(self, to_status: SkillLifecycleStatus, min_eval_score: float = 0.85) -> bool:
        """Transiciona o ciclo de vida garantindo gates de qualidade e segurança."""
        allowed_transitions = {
            "candidate": ["sandbox", "deprecated"],
            "sandbox": ["eval", "deprecated"],
            "eval": ["active", "sandbox", "deprecated"],
            "active": ["deprecated"],
            "deprecated": ["candidate"],
        }

        if to_status not in allowed_transitions.get(self.status, []):
            raise ValueError(
                f"Transição inválida de ciclo de vida: {self.status} -> {to_status}"
            )

        # Gate de ativação: exige avaliação prévia e score satisfatório
        if to_status == "active":
            if self.eval_score is None or self.eval_score < min_eval_score:
                raise PermissionError(
                    f"Skill {self.name} não pode ser ativada sem aprovação no gate de eval "
                    f"(score atual: {self.eval_score}, mínimo exigido: {min_eval_score})"
                )

        self.status = to_status
        self.updated_at = time.time()
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "license": self.license,
            "status": self.status,
            "capabilities_required": list(self.capabilities_required),
            "preferred_posture": self.preferred_posture,
            "preferred_model_family": self.preferred_model_family,
            "dependencies": list(self.dependencies),
            "entry_script": self.entry_script,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "eval_score": self.eval_score,
            "checksum_sha256": self.checksum_sha256,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SkillSpec:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            author=data.get("author", "Adriano + HAOS"),
            license=data.get("license", "MIT"),
            status=data.get("status", "candidate"),
            capabilities_required=list(data.get("capabilities_required", [])),
            preferred_posture=data.get("preferred_posture", "coder"),
            preferred_model_family=data.get("preferred_model_family"),
            dependencies=list(data.get("dependencies", [])),
            entry_script=data.get("entry_script"),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            eval_score=float(data["eval_score"]) if data.get("eval_score") is not None else None,
            checksum_sha256=data.get("checksum_sha256", ""),
            metadata=dict(data.get("metadata", {})),
        )
