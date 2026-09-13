"""ContextWire — Serialização e empacotamento seguro de ContextPackages sobre o Protocol Fabric (A2A / ANP).

Garante que a troca de mensagens entre agentes independentes e revisores transmita:
1. ContextPackages tipados com hashes criptográficos e proveniência (em vez de transcrição bruta).
2. Filtragem de postura na borda (Clean Context Enforcement):
   Ex.: Se a mensagem é destinada a um agente com postura 'reviewer', itens não autorizados
   (como 'coder_chain_of_thought') são sumariamente expurgados antes da transmissão no wire.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional

from hermes.platform.context.policies.policy import resolve_context_policy
from hermes.platform.context.primitives.item import AuthorityLevel, ContextItem, TrustLevel
from hermes.platform.context.primitives.package import ContextPackage


def serialize_context_item(item: ContextItem) -> Dict[str, Any]:
    """Serializa um ContextItem para dicionário serializável em JSON."""
    return {
        "id": item.id,
        "item_type": item.item_type,
        "source_uri": item.source_uri,
        "content": item.content,
        "title": item.title,
        "summary": item.summary,
        "abstract": item.abstract,
        "provenance": item.provenance,
        "trust": item.trust.value,
        "authority": item.authority.value,
        "freshness": item.freshness,
        "relevance": item.relevance,
        "token_cost": item.token_cost,
        "immutable": item.immutable,
        "scope": item.scope,
        "metadata": item.metadata,
    }


def deserialize_context_item(data: Dict[str, Any]) -> ContextItem:
    """Reconstrói um ContextItem preservando níveis de confiança e autoridade."""
    trust_val = data.get("trust", TrustLevel.TRUSTED_INTERNAL_ARTIFACT.value)
    auth_val = data.get("authority", AuthorityLevel.ADVISORY.value)
    return ContextItem(
        id=data["id"],
        item_type=data["item_type"],
        source_uri=data["source_uri"],
        content=data["content"],
        title=data.get("title", ""),
        summary=data.get("summary", ""),
        abstract=data.get("abstract", ""),
        provenance=data.get("provenance", "canonical"),
        trust=TrustLevel(trust_val),
        authority=AuthorityLevel(auth_val),
        freshness=data.get("freshness", ""),
        relevance=float(data.get("relevance", 1.0)),
        token_cost=int(data.get("token_cost", 0)),
        immutable=bool(data.get("immutable", False)),
        scope=data.get("scope", "project"),
        metadata=dict(data.get("metadata", {})),
    )


def serialize_context_package(package: ContextPackage) -> Dict[str, Any]:
    """Serializa um ContextPackage para transmissão na rede."""
    serialized_sections: Dict[str, List[Dict[str, Any]]] = {}
    for sec_name, items in package.sections.items():
        serialized_sections[sec_name] = [serialize_context_item(it) for it in items]

    return {
        "id": package.id,
        "task_id": package.task_id,
        "task_revision": package.task_revision,
        "posture_id": package.posture_id,
        "budget_limit": package.budget_limit,
        "token_count": package.token_count,
        "digest_hash": package.digest_hash,
        "representations": dict(package.representations),
        "sections": serialized_sections,
    }


def deserialize_context_package(data: Dict[str, Any]) -> ContextPackage:
    """Reconstitui o ContextPackage a partir do payload recebido pelo wire."""
    sections: Dict[str, List[ContextItem]] = {}
    raw_sections = data.get("sections", {})
    for sec_name, items_data in raw_sections.items():
        sections[sec_name] = [deserialize_context_item(it) for it in items_data]

    pkg = ContextPackage(
        id=data["id"],
        task_id=data["task_id"],
        task_revision=data["task_revision"],
        posture_id=data["posture_id"],
        budget_limit=data.get("budget_limit", 64000),
        sections=sections,
        representations=dict(data.get("representations", {})),
        token_count=data.get("token_count", 0),
        digest_hash=data.get("digest_hash", ""),
    )
    return pkg


def wrap_context_for_agent(
    task_id: str,
    context_package: ContextPackage,
    recipient_posture: str,
) -> Dict[str, Any]:
    """Aplica Clean Context Enforcement no wire antes do envio para o agente destinatário.
    
    Se o destinatário for um Reviewer, expurga qualquer item proibido pela política de revisão
    (ex.: coder_chain_of_thought, notas internas não verificadas).
    """
    policy = resolve_context_policy(recipient_posture)
    filtered_sections: Dict[str, List[ContextItem]] = {}
    stripped_count = 0

    for sec_name, items in context_package.sections.items():
        filtered_items = []
        for it in items:
            allowed, _ = policy.is_item_allowed(it)
            if allowed:
                filtered_items.append(it)
            else:
                stripped_count += 1
        if filtered_items:
            filtered_sections[sec_name] = filtered_items

    clean_package = ContextPackage(
        id=f"clean-{context_package.id}",
        task_id=task_id,
        task_revision=context_package.task_revision,
        posture_id=recipient_posture,
        budget_limit=policy.max_tokens,
        sections=filtered_sections,
        representations=copy.deepcopy(context_package.representations),
    )
    clean_package.recalculate_digest_and_tokens()

    return {
        "status": "enveloped",
        "recipient_posture": recipient_posture,
        "stripped_items_count": stripped_count,
        "package": serialize_context_package(clean_package),
    }
