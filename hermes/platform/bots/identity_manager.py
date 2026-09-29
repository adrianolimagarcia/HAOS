"""IdentityManager — Event-sourced lifecycle and version manager for Bot identities.

Backed by EventStore; all state is derived from immutable civilization events:
- civ.bot.identity-version-created
- civ.bot.identity-version-activated
- civ.bot.identity-drift-detected
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from .identity import BotIdentityBundle, IdentityVersion

logger = logging.getLogger("hermes.platform.bots.identity_manager")

_VERSION_CREATED = "civ.bot.identity-version-created"
_VERSION_ACTIVATED = "civ.bot.identity-version-activated"
_DRIFT_DETECTED = "civ.bot.identity-drift-detected"


class IdentityManager:
    """Event-sourced repository managing versioned Bot identities."""

    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    def _events(self, bot_id: Optional[str] = None) -> List[Event]:
        events = [
            e
            for e in self.event_store.get_all()
            if e.name in (_VERSION_CREATED, _VERSION_ACTIVATED, _DRIFT_DETECTED)
        ]
        if bot_id is not None:
            events = [e for e in events if e.payload.get("bot_id") == bot_id]
        return events

    def _state(self) -> Dict[str, Dict[str, Any]]:
        """Materialize current projection of all bots' identity versions and active pointers."""
        # state = {bot_id: {"versions": {version_id: IdentityVersion}, "active_id": Optional[str]}}
        state: Dict[str, Dict[str, Any]] = {}
        for e in self._events():
            bot_id = e.payload.get("bot_id")
            if not bot_id:
                continue
            if bot_id not in state:
                state[bot_id] = {"versions": {}, "active_id": None}

            if e.name == _VERSION_CREATED:
                ver_dict = e.payload.get("version")
                if ver_dict:
                    v = IdentityVersion.from_dict(ver_dict)
                    state[bot_id]["versions"][v.id] = v
                    if state[bot_id]["active_id"] is None and v.status == "active":
                        state[bot_id]["active_id"] = v.id

            elif e.name == _VERSION_ACTIVATED:
                ver_id = e.payload.get("version_id")
                if ver_id and ver_id in state[bot_id]["versions"]:
                    state[bot_id]["active_id"] = ver_id

        return state

    def create_version(
        self,
        bot_id: str,
        bundle: BotIdentityBundle,
        parent_id: Optional[str] = None,
        activate: bool = True,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> IdentityVersion:
        """Create a new immutable IdentityVersion from bundle and optionally activate it."""
        state = self._state()
        bot_state = state.get(bot_id, {"versions": {}, "active_id": None})
        existing_versions = bot_state["versions"]

        next_version_num = (
            max([v.version for v in existing_versions.values()], default=0) + 1
        )
        version_id = f"{bot_id}-v{next_version_num}-{uuid.uuid4().hex[:8]}"

        version = IdentityVersion(
            id=version_id,
            bot_id=bot_id,
            version=next_version_num,
            bundle_hash=bundle.bundle_hash,
            parent_id=parent_id or bot_state["active_id"],
            bundle=bundle,
            status="active" if activate else "draft",
        )

        event = Event(
            name=_VERSION_CREATED,
            payload={"bot_id": bot_id, "version": version.to_dict()},
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        self.event_store.append(event)

        if activate:
            self.activate_version(
                bot_id=bot_id,
                version_id=version_id,
                correlation_id=correlation_id,
                causation_id=event.event_id,
            )

        return version

    def activate_version(
        self,
        bot_id: str,
        version_id: str,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> IdentityVersion:
        """Set version_id as the active identity for bot_id."""
        version = self.get_version(bot_id, version_id)
        if version is None:
            raise KeyError(f"IdentityVersion {version_id} not found for bot {bot_id}")

        self.event_store.append(
            Event(
                name=_VERSION_ACTIVATED,
                payload={"bot_id": bot_id, "version_id": version_id},
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        )
        return version

    def get_version(self, bot_id: str, version_id: str) -> Optional[IdentityVersion]:
        """Get a specific IdentityVersion by id."""
        state = self._state()
        bot_state = state.get(bot_id)
        if not bot_state:
            return None
        return bot_state["versions"].get(version_id)

    def get_active_version(self, bot_id: str) -> Optional[IdentityVersion]:
        """Get the currently active IdentityVersion for bot_id."""
        state = self._state()
        bot_state = state.get(bot_id)
        if not bot_state or not bot_state["active_id"]:
            return None
        return bot_state["versions"].get(bot_state["active_id"])

    def list_versions(self, bot_id: str) -> List[IdentityVersion]:
        """List all IdentityVersions for bot_id ordered by version number."""
        state = self._state()
        bot_state = state.get(bot_id)
        if not bot_state:
            return []
        return sorted(bot_state["versions"].values(), key=lambda v: v.version)

    def rollback(
        self,
        bot_id: str,
        target_version_id: str,
        reason: str = "compensating rollback",
        correlation_id: Optional[str] = None,
    ) -> IdentityVersion:
        """Rollback creates a new version with the target's content (preserves immutable history)."""
        target = self.get_version(bot_id, target_version_id)
        if target is None or target.bundle is None:
            raise KeyError(f"Target version {target_version_id} not found or missing bundle")

        # Create compensatory new version restoring target content
        new_bundle = BotIdentityBundle(
            bot_id=bot_id,
            soul=target.bundle.soul,
            identity=target.bundle.identity,
            values=target.bundle.values,
            metadata={"rollback_from": target_version_id, "rollback_reason": reason},
        )
        return self.create_version(
            bot_id=bot_id,
            bundle=new_bundle,
            parent_id=target_version_id,
            activate=True,
            correlation_id=correlation_id,
        )

    def record_drift(
        self,
        bot_id: str,
        active_version_id: str,
        detected_bundle_hash: str,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Emit drift event when on-disk files differ from active version."""
        self.event_store.append(
            Event(
                name=_DRIFT_DETECTED,
                payload={
                    "bot_id": bot_id,
                    "active_version_id": active_version_id,
                    "detected_bundle_hash": detected_bundle_hash,
                },
                correlation_id=correlation_id,
            )
        )
