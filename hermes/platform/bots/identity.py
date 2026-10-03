"""Canonical domain models for HAOS Civilization (V1 Foundation).

Implements BotIdentityBundle, IdentityVersion, LeafIdentitySnapshot,
CouncilSpec, and DecisionRecord according to haos-civ/v1/specs/DATA_MODELS.md
and haos-civ/README.md.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


def compute_sha256(text: str) -> str:
    """Deterministic SHA-256 digest of utf-8 text."""
    if not text:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_bundle_hash(soul: str, identity: str, values: str) -> str:
    """Canonical hash over identity bundle components."""
    hasher = hashlib.sha256()
    hasher.update(b"soul:")
    hasher.update(soul.encode("utf-8"))
    hasher.update(b"|identity:")
    hasher.update(identity.encode("utf-8"))
    hasher.update(b"|values:")
    hasher.update(values.encode("utf-8"))
    return hasher.hexdigest()


@dataclass(frozen=True)
class BotIdentitySpec:
    """Reference specification in BotSpec pointing to identity assets."""
    soul: str = "SOUL.md"
    identity: str = "IDENTITY.md"
    values: str = "VALUES.md"
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotIdentitySpec":
        return cls(
            soul=data.get("soul", "SOUL.md"),
            identity=data.get("identity", "IDENTITY.md"),
            values=data.get("values", "VALUES.md"),
            version=int(data.get("version", 1)),
        )


@dataclass(frozen=True)
class BotIdentityBundle:
    """Resolved content bundle of a Bot's identity files."""
    bot_id: str
    identity_version: int = 1
    soul: str = ""
    identity: str = ""
    values: str = ""
    soul_hash: str = ""
    identity_hash: str = ""
    values_hash: str = ""
    bundle_hash: str = ""
    schema_version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.bot_id.strip():
            raise ValueError("bot_id is required")
        # Compute hashes if empty
        if not self.soul_hash:
            object.__setattr__(self, "soul_hash", compute_sha256(self.soul))
        if not self.identity_hash:
            object.__setattr__(self, "identity_hash", compute_sha256(self.identity))
        if not self.values_hash:
            object.__setattr__(self, "values_hash", compute_sha256(self.values))
        if not self.bundle_hash:
            object.__setattr__(
                self, "bundle_hash", compute_bundle_hash(self.soul, self.identity, self.values)
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BotIdentityBundle":
        fields = {
            k: data[k]
            for k in (
                "bot_id",
                "identity_version",
                "soul",
                "identity",
                "values",
                "soul_hash",
                "identity_hash",
                "values_hash",
                "bundle_hash",
                "schema_version",
                "metadata",
            )
            if k in data
        }
        return cls(**fields)


@dataclass(frozen=True)
class IdentityVersion:
    """Immutable versioned record of a Bot's identity."""
    id: str
    bot_id: str
    version: int
    bundle_hash: str
    parent_id: Optional[str] = None
    bundle: Optional[BotIdentityBundle] = None
    status: str = "active"  # active, draft, pending, deprecated, archived
    created_at: float = field(default_factory=time.time)
    schema_version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("IdentityVersion id is required")
        if not self.bot_id.strip():
            raise ValueError("bot_id is required")
        if self.version < 1:
            raise ValueError("version must be positive")

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        if self.bundle:
            res["bundle"] = self.bundle.to_dict()
        return res

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IdentityVersion":
        bundle_data = data.get("bundle")
        bundle = BotIdentityBundle.from_dict(bundle_data) if isinstance(bundle_data, dict) else None
        return cls(
            id=data["id"],
            bot_id=data["bot_id"],
            version=int(data["version"]),
            bundle_hash=data["bundle_hash"],
            parent_id=data.get("parent_id"),
            bundle=bundle,
            status=data.get("status", "active"),
            created_at=float(data.get("created_at", time.time())),
            schema_version=int(data.get("schema_version", 1)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class LeafIdentitySnapshot:
    """Immutable proof of origin and context frozen for an ephemeral Leaf execution."""
    leaf_id: str
    parent_bot_id: str
    identity_version_id: str
    bot_identity_version: int = 1
    identity_bundle_hash: str = ""
    temporary_soul: str = ""
    temporary_soul_hash: str = ""
    prompt_hash: str = ""
    toolset_hash: str = ""
    memory_snapshot_ref: Optional[str] = None
    council_id: Optional[str] = None
    council_session_id: Optional[str] = None
    model: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: Optional[float] = None
    causation_id: Optional[str] = None
    correlation_id: Optional[str] = None
    schema_version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.leaf_id.strip():
            raise ValueError("leaf_id is required")
        if not self.parent_bot_id.strip():
            raise ValueError("parent_bot_id is required")
        if not self.identity_version_id.strip():
            raise ValueError("identity_version_id is required")
        if not self.temporary_soul_hash and self.temporary_soul:
            object.__setattr__(self, "temporary_soul_hash", compute_sha256(self.temporary_soul))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LeafIdentitySnapshot":
        fields = {
            k: data[k]
            for k in (
                "leaf_id",
                "parent_bot_id",
                "identity_version_id",
                "bot_identity_version",
                "identity_bundle_hash",
                "temporary_soul",
                "temporary_soul_hash",
                "prompt_hash",
                "toolset_hash",
                "memory_snapshot_ref",
                "council_id",
                "council_session_id",
                "model",
                "created_at",
                "expires_at",
                "causation_id",
                "correlation_id",
                "schema_version",
                "metadata",
            )
            if k in data
        }
        return cls(**fields)


@dataclass(frozen=True)
class CouncilSpec:
    """First-class specification of a multi-bot Council aggregate."""
    id: str
    purpose: str
    members: List[str] = field(default_factory=list)  # bot_ids
    roles: Dict[str, str] = field(default_factory=dict)  # bot_id -> role name
    decision_mode: str = "consensus_with_dissent"  # single_synthesizer, majority, consensus_with_dissent
    budget: Dict[str, Any] = field(default_factory=dict)
    policy_ref: Optional[str] = None
    rules: List[str] = field(default_factory=list)
    version: int = 1
    schema_version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("CouncilSpec id is required")
        if not self.purpose.strip():
            raise ValueError("CouncilSpec purpose is required")
        if self.decision_mode not in ("single_synthesizer", "majority", "consensus_with_dissent"):
            raise ValueError(f"invalid decision_mode: {self.decision_mode}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CouncilSpec":
        fields = {
            k: data[k]
            for k in (
                "id",
                "purpose",
                "members",
                "roles",
                "decision_mode",
                "budget",
                "policy_ref",
                "rules",
                "version",
                "schema_version",
                "metadata",
            )
            if k in data
        }
        return cls(**fields)


@dataclass(frozen=True)
class DecisionRecord:
    """Audit record capturing positions, evidence, synthesis, and dissent."""
    id: str
    council_id: str
    council_session_id: str
    objective: str
    participants: List[str] = field(default_factory=list)  # bot_ids
    identity_version_refs: Dict[str, str] = field(default_factory=dict)  # bot_id -> identity_version_id
    leaf_refs: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    positions: Dict[str, Any] = field(default_factory=dict)  # bot_id -> position
    synthesis: str = ""
    dissent: Dict[str, str] = field(default_factory=dict)  # bot_id -> dissent reason
    decision: str = ""
    confidence: float = 1.0
    action_refs: List[str] = field(default_factory=list)
    policy_verified: bool = True
    # Deliberation provenance (audit hardening): "real" means member positions
    # were produced by an actual model executor (MemberRunner with a non-mock
    # LeafExecutor); "facade" means positions were scripted or produced by the
    # deterministic mock executor and are NOT evidence of model deliberation.
    # Fail-closed default: an unset provenance is treated as "facade".
    deliberation: str = "facade"
    created_at: float = field(default_factory=time.time)
    correlation_id: Optional[str] = None
    schema_version: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("DecisionRecord id is required")
        if not self.council_id.strip():
            raise ValueError("council_id is required")
        if not self.council_session_id.strip():
            raise ValueError("council_session_id is required")
        if self.deliberation not in ("real", "facade"):
            raise ValueError(
                f"invalid deliberation provenance: {self.deliberation!r} "
                "(must be 'real' or 'facade')"
            )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionRecord":
        fields = {
            k: data[k]
            for k in (
                "id",
                "council_id",
                "council_session_id",
                "objective",
                "participants",
                "identity_version_refs",
                "leaf_refs",
                "evidence_refs",
                "positions",
                "synthesis",
                "dissent",
                "decision",
                "confidence",
                "action_refs",
                "policy_verified",
                "deliberation",
                "created_at",
                "correlation_id",
                "schema_version",
                "metadata",
            )
            if k in data
        }
        return cls(**fields)
