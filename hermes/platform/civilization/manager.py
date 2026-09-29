"""Event-sourced CivilizationManager for Constitution enforcement and shared memory."""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event

from .models import (
    EVENT_ASSERTION_RECORDED,
    EVENT_CONSTITUTION_ENACTED,
    EVENT_POLICY_EVALUATED,
    POLICY_RESULT_ALLOW,
    POLICY_RESULT_DENY,
    POLICY_RESULT_REQUIRE_APPROVAL,
    RULE_TYPE_ADVISORY,
    RULE_TYPE_HARD_DENY,
    ConstitutionRule,
    ConstitutionVersion,
    KnowledgeAssertion,
    PolicyDecision,
)


class CivilizationManager:
    """Oversees constitutional rules, policy evaluations, and civilization memory."""

    def __init__(self, event_store: EventStore) -> None:
        self.store = event_store

    def _events(self) -> List[Event]:
        return [
            e
            for e in self.store.get_all()
            if e.name.startswith("civ.")
        ]

    def enact_constitution(
        self,
        version: int,
        title: str,
        rules: List[ConstitutionRule],
        approved_by: str,
    ) -> ConstitutionVersion:
        cv = ConstitutionVersion(
            version=version,
            title=title,
            rules=rules,
            approved_by=approved_by,
            effective_at=time.time(),
        )

        self.store.append(
            Event(
                name=EVENT_CONSTITUTION_ENACTED,
                payload={"constitution": cv.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return cv

    def get_active_constitution(self) -> Optional[ConstitutionVersion]:
        active: Optional[ConstitutionVersion] = None
        for e in self._events():
            if e.name == EVENT_CONSTITUTION_ENACTED:
                payload = e.payload or {}
                if "constitution" in payload:
                    cv = ConstitutionVersion.from_dict(payload["constitution"])
                    if active is None or cv.version > active.version:
                        active = cv
        return active

    def evaluate_policy(
        self,
        subject_bot: str,
        action: str,
        resource: str,
    ) -> PolicyDecision:
        active = self.get_active_constitution()
        result = POLICY_RESULT_ALLOW
        matched_rule: Optional[str] = None
        reason = "No restrictive rule matched"

        if active:
            for rule in active.rules:
                if rule.target_action == "*" or rule.target_action == action:
                    if rule.rule_type == RULE_TYPE_HARD_DENY:
                        result = POLICY_RESULT_DENY
                        matched_rule = rule.id
                        reason = f"Hard deny by rule '{rule.name}': {rule.description}"
                        break
                    elif rule.rule_type == RULE_TYPE_ADVISORY and result == POLICY_RESULT_ALLOW:
                        result = POLICY_RESULT_REQUIRE_APPROVAL
                        matched_rule = rule.id
                        reason = f"Advisory warning by rule '{rule.name}': {rule.description}"

        dec_id = f"pol-{uuid.uuid4().hex[:12]}"
        dec = PolicyDecision(
            id=dec_id,
            rule_id=matched_rule,
            subject_bot=subject_bot,
            action=action,
            resource=resource,
            result=result,
            reason=reason,
            evaluated_at=time.time(),
        )

        self.store.append(
            Event(
                name=EVENT_POLICY_EVALUATED,
                payload={"decision": dec.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return dec

    def record_assertion(
        self,
        subject: str,
        predicate: str,
        object_: str,
        confidence: float,
        provenance_ref: str,
    ) -> KnowledgeAssertion:
        assert_id = f"assert-{uuid.uuid4().hex[:12]}"
        assertion = KnowledgeAssertion(
            id=assert_id,
            subject=subject,
            predicate=predicate,
            object=object_,
            confidence=confidence,
            provenance_ref=provenance_ref,
            valid_from=time.time(),
            valid_to=None,
        )

        self.store.append(
            Event(
                name=EVENT_ASSERTION_RECORDED,
                payload={"assertion": assertion.to_dict()},
                event_id=f"evt-{uuid.uuid4().hex[:12]}",
            )
        )
        return assertion

    def query_knowledge(
        self,
        subject: Optional[str] = None,
        predicate: Optional[str] = None,
    ) -> List[KnowledgeAssertion]:
        results: List[KnowledgeAssertion] = []
        for e in self._events():
            if e.name == EVENT_ASSERTION_RECORDED:
                payload = e.payload or {}
                if "assertion" in payload:
                    a = KnowledgeAssertion.from_dict(payload["assertion"])
                    if subject and a.subject != subject:
                        continue
                    if predicate and a.predicate != predicate:
                        continue
                    results.append(a)
        return results
