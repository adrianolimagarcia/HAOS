"""Memory Architecture with Strict Provenance and Tiered Hierarchy.

Enforces:
1. Complete provenance on every memory record:
   {
     "id": "...",
     "value": "...",
     "source_agent": "...",
     "task_id": "...",
     "confidence": 0.95,
     "created": 1718000000.0,
     "validated_by": ["critic-bot", "verifier-bot"],
     "signature": "sha256...",
     "parent_id": "..."
   }

2. Tiered Promotion Hierarchy:
   WORKING -> SESSION -> PROJECT -> DOMAIN -> GLOBAL_SKILLS

3. Validation Gate:
   No memory can be promoted beyond SESSION without independent validator signature.
"""

from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Set
import uuid

from hermes.platform.kernel.contract import (
    AgentRole,
    AgentRuntimeContract,
    ContractViolationError,
    MemoryScope,
)


class MemoryValidationError(Exception):
    """Raised when a memory operation violates provenance or promotion invariants."""
    pass


@dataclass
class MemoryItem:
    memory_id: str
    scope: MemoryScope
    key: str
    value: Any
    source_agent: str
    task_id: str
    confidence: float
    created_at: float = field(default_factory=time.time)
    validated_by: List[str] = field(default_factory=list)
    signature: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.signature:
            self.signature = self.compute_signature()

    @property
    def id(self) -> str:
        return self.memory_id

    @property
    def parent_id(self) -> Optional[str]:
        return self.metadata.get("parent_id")

    def compute_signature(self) -> str:
        payload = f"{self.key}:{json.dumps(self.value, sort_keys=True)}:{self.source_agent}:{self.task_id}:{self.scope.value}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def is_validated(self) -> bool:
        """Returns True if validated by at least one independent agent."""
        return len(self.validated_by) > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.memory_id,
            "memory_id": self.memory_id,
            "scope": self.scope.value,
            "key": self.key,
            "value": self.value,
            "source_agent": self.source_agent,
            "task_id": self.task_id,
            "confidence": round(self.confidence, 4),
            "created": self.created_at,
            "created_at": self.created_at,
            "validated_by": list(self.validated_by),
            "signature": self.signature,
            "parent_id": self.parent_id,
            "metadata": dict(self.metadata),
        }


class MemoryHierarchyStore:
    """Store managing partitioned tiered memory with provenance and promotion controls."""

    def __init__(self) -> None:
        # Partition by scope
        self._stores: Dict[MemoryScope, Dict[str, MemoryItem]] = {
            scope: {} for scope in MemoryScope
        }

    def write(
        self,
        key: str = "",
        value: Any = None,
        task_id: str = "default",
        confidence: float = 0.5,
        target_scope: MemoryScope = MemoryScope.WORKING,
        scope: Optional[MemoryScope] = None,
        contract: Optional[AgentRuntimeContract] = None,
        source_agent: str = "system",
        validated_by: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> MemoryItem:
        """Write a new memory record, respecting contract write scopes."""
        if scope is not None:
            target_scope = scope
        if not key:
            key = f"key-{uuid.uuid4().hex[:8]}"

        if contract is not None:
            source_agent = contract.identity.agent_id
            if not contract.memory.can_write(target_scope):
                raise ContractViolationError(
                    f"Agent '{contract.identity.agent_id}' with role '{contract.identity.role.value}' "
                    f"is not authorized to write to memory scope '{target_scope.value}'"
                )

        # Invariant: Permanent scopes (PROJECT, DOMAIN, GLOBAL) cannot be written directly without validation
        if target_scope in {MemoryScope.PROJECT, MemoryScope.DOMAIN, MemoryScope.GLOBAL}:
            if not validated_by or len(validated_by) == 0:
                raise MemoryValidationError(
                    f"Direct write to {target_scope.value} memory requires prior validation evidence"
                )

        mem_id = f"mem-{uuid.uuid4().hex[:12]}"
        item = MemoryItem(
            memory_id=mem_id,
            scope=target_scope,
            key=key,
            value=value,
            source_agent=source_agent,
            task_id=task_id,
            confidence=confidence,
            validated_by=list(validated_by or []),
            metadata=dict(metadata or {}),
        )
        self._stores[target_scope][mem_id] = item
        return item

    def get(self, memory_id: str) -> Optional[MemoryItem]:
        for items in self._stores.values():
            if memory_id in items:
                return items[memory_id]
        return None

    def read(
        self,
        contract: Optional[AgentRuntimeContract] = None,
        scope: Optional[MemoryScope] = None,
        key: Optional[str] = None,
        memory_id: Optional[str] = None,
    ) -> Any:
        """Read memories from an authorized scope, or by memory_id."""
        if memory_id:
            item = self.get(memory_id)
            if item and contract and not contract.memory.can_read(item.scope):
                raise ContractViolationError(
                    f"Agent '{contract.identity.agent_id}' cannot read from memory scope '{item.scope.value}'"
                )
            return item

        target_scope = scope or MemoryScope.WORKING
        if contract and not contract.memory.can_read(target_scope):
            raise ContractViolationError(
                f"Agent '{contract.identity.agent_id}' cannot read from memory scope '{target_scope.value}'"
            )

        items = list(self._stores[target_scope].values())
        if key:
            items = [item for item in items if item.key == key]
        return items

    def add_validation(
        self,
        memory_id: str,
        validator_agent_id: Optional[str] = None,
        validator_role: Optional[AgentRole] = None,
        validator_agent: Optional[str] = None,
        confidence_score: Optional[float] = None,
        critique_notes: str = "",
        **kwargs: Any,
    ) -> MemoryItem:
        """Attach validation signoff to an existing memory item."""
        val_agent = validator_agent_id or validator_agent or "validator"
        for scope_items in self._stores.values():
            if memory_id in scope_items:
                item = scope_items[memory_id]
                # Invariant: Producer cannot validate its own memory (anti-confirmation bias)
                if item.source_agent == val_agent:
                    raise MemoryValidationError(
                        f"Agent '{val_agent}' cannot validate its own memory record '{memory_id}'"
                    )
                if val_agent not in item.validated_by:
                    item.validated_by.append(val_agent)
                if critique_notes:
                    if "critique_notes" not in item.metadata:
                        item.metadata["critique_notes"] = []
                    item.metadata["critique_notes"].append(critique_notes)
                # Boost confidence upon independent validation
                boost = 0.20 if confidence_score is None else max(0.05, confidence_score - item.confidence)
                item.confidence = min(1.0, item.confidence + boost)
                return item

        raise KeyError(f"Memory item '{memory_id}' not found in any scope")

    def promote(
        self,
        memory_id: str,
        target_scope: MemoryScope,
        promoter_contract: Optional[AgentRuntimeContract] = None,
        promoter_agent: Optional[str] = None,
        **kwargs: Any,
    ) -> MemoryItem:
        """Promote a memory item to a higher scope through the validation gate."""
        source_item: Optional[MemoryItem] = None
        source_scope: Optional[MemoryScope] = None

        for sc, items in self._stores.items():
            if memory_id in items:
                source_item = items[memory_id]
                source_scope = sc
                break

        if not source_item or not source_scope:
            raise KeyError(f"Memory item '{memory_id}' not found")

        # Invariant 1: Cannot demote
        scope_rank = {
            MemoryScope.WORKING: 1,
            MemoryScope.SESSION: 2,
            MemoryScope.PROJECT: 3,
            MemoryScope.DOMAIN: 4,
            MemoryScope.GLOBAL: 5,
        }
        if scope_rank[target_scope] <= scope_rank[source_scope]:
            raise MemoryValidationError(
                f"Cannot promote memory from {source_scope.value} to equal/lower scope {target_scope.value}"
            )

        # Invariant 2: Must be validated by an independent agent before promotion past SESSION
        if scope_rank[target_scope] >= scope_rank[MemoryScope.PROJECT]:
            if not source_item.is_validated():
                raise MemoryValidationError(
                    f"Memory '{memory_id}' is not validated by any critic and cannot be promoted to {target_scope.value}"
                )

        # Invariant 3: Global promotion requires PROMOTER role
        if target_scope == MemoryScope.GLOBAL and promoter_contract:
            if promoter_contract.identity.role != AgentRole.PROMOTER:
                raise ContractViolationError(
                    f"Only PROMOTER role may promote memory to GLOBAL (agent has {promoter_contract.identity.role.value})"
                )

        promoted_id = f"mem-prom-{uuid.uuid4().hex[:8]}"
        promoter_name = promoter_contract.identity.agent_id if promoter_contract else (promoter_agent or "promoter")
        meta = dict(source_item.metadata)
        meta["promoted_from"] = source_item.memory_id
        meta["promoter_agent"] = promoter_name
        meta["parent_id"] = source_item.memory_id

        promoted_item = MemoryItem(
            memory_id=promoted_id,
            scope=target_scope,
            key=source_item.key,
            value=source_item.value,
            source_agent=source_item.source_agent,
            task_id=source_item.task_id,
            confidence=source_item.confidence,
            validated_by=list(source_item.validated_by),
            metadata=meta,
        )
        self._stores[target_scope][promoted_id] = promoted_item
        return promoted_item

    def dump_provenance(self, memory_id: str) -> Optional[Dict[str, Any]]:
        for items in self._stores.values():
            if memory_id in items:
                return items[memory_id].to_dict()
        return None


TieredMemoryArchitecture = MemoryHierarchyStore
