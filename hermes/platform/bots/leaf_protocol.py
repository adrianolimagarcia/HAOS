"""Leaf Execution Protocol implementation.

Provides deterministic temporary SOUL derivation and immutable snapshot creation
for ephemeral Leaf executions derived from persistent parent Bots.
"""

from __future__ import annotations

from typing import List, Optional

from .identity import (
    IdentityVersion,
    LeafIdentitySnapshot,
    compute_sha256,
)


def build_temporary_soul(
    parent_soul: str,
    task_description: str,
    constraints: Optional[List[str]] = None,
    council_context: Optional[str] = None,
) -> str:
    """Deterministically assemble a temporary SOUL for a Leaf mission."""
    parts = []
    if parent_soul and parent_soul.strip():
        parts.append(parent_soul.strip())
    if task_description and task_description.strip():
        parts.append(f"## Mission Focus\n{task_description.strip()}")
    if constraints:
        formatted_constraints = "\n".join(f"- {c.strip()}" for c in constraints if c.strip())
        if formatted_constraints:
            parts.append(f"## Mission Constraints\n{formatted_constraints}")
    if council_context and council_context.strip():
        parts.append(f"## Council Context\n{council_context.strip()}")
    return "\n\n".join(parts)


def create_leaf_identity_snapshot(
    leaf_id: str,
    parent_bot_id: str,
    identity_version: IdentityVersion,
    task_description: str,
    constraints: Optional[List[str]] = None,
    council_id: Optional[str] = None,
    council_session_id: Optional[str] = None,
    model: str = "",
    toolset_hash: str = "",
    memory_snapshot_ref: Optional[str] = None,
    correlation_id: Optional[str] = None,
    causation_id: Optional[str] = None,
) -> LeafIdentitySnapshot:
    """Create an immutable LeafIdentitySnapshot freezing the execution context."""
    bundle = identity_version.bundle
    parent_soul = bundle.soul if bundle else ""
    temp_soul = build_temporary_soul(
        parent_soul=parent_soul,
        task_description=task_description,
        constraints=constraints,
        council_context=council_id,
    )
    temp_soul_hash = compute_sha256(temp_soul)
    bundle_hash = identity_version.bundle_hash

    return LeafIdentitySnapshot(
        leaf_id=leaf_id,
        parent_bot_id=parent_bot_id,
        identity_version_id=identity_version.id,
        bot_identity_version=identity_version.version,
        identity_bundle_hash=bundle_hash,
        temporary_soul=temp_soul,
        temporary_soul_hash=temp_soul_hash,
        prompt_hash="",
        toolset_hash=toolset_hash,
        memory_snapshot_ref=memory_snapshot_ref,
        council_id=council_id,
        council_session_id=council_session_id,
        model=model,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )
