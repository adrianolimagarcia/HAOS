"""Event-sourced BotSpec registry; the EventStore remains the only ledger."""
from dataclasses import replace
from typing import Any, Dict, List, Optional
import uuid
from hermes.platform.observability.event_store import EventStore
from hermes.platform.observability.events import Event
from hermes.platform.tasks.spec import TaskSpec
from .spec import BotSpec

_REGISTERED = "bot.spec.registered"
_UPDATED = "bot.spec.updated"
_DELETED = "bot.spec.deleted"
_PAUSED = "bot.spec.paused"
_RESUMED = "bot.spec.resumed"
_DUPLICATED = "bot.spec.duplicated"
_ROUTINE_SUBMITTED = "bot.routine.submitted"
_RUN_RECORDED = "bot.routine.run_recorded"

# Canonical terminal lifecycle states emitted at the dispatcher seam.
RUN_STATES = frozenset({"submitted", "claimed", "running", "completed", "failed", "cancelled", "blocked"})

class BotSpecManager:
    def __init__(self, event_store: EventStore):
        self.event_store = event_store

    def _state(self) -> Dict[str, BotSpec]:
        state: Dict[str, BotSpec] = {}
        for event in self.event_store.get_all():
            if event.name not in (_REGISTERED, _UPDATED, _DELETED, _DUPLICATED): continue
            bot_id = event.payload.get("bot_id")
            if event.name == _DELETED: state.pop(bot_id, None)
            elif bot_id: state[bot_id] = BotSpec.from_dict(event.payload["spec"])
        return state

    def _paused(self) -> set[str]:
        paused: set[str] = set()
        for event in self.event_store.get_all():
            if event.name == _PAUSED: paused.add(event.payload["bot_id"])
            elif event.name == _RESUMED: paused.discard(event.payload["bot_id"])
            elif event.name == _DELETED: paused.discard(event.payload["bot_id"])
        return paused

    def register(self, spec: BotSpec) -> BotSpec:
        if spec.id in self._state(): raise ValueError(f"BotSpec already exists: {spec.id}")
        self._append(_REGISTERED, spec); return spec

    def update(self, spec: BotSpec) -> BotSpec:
        if spec.id not in self._state(): raise KeyError(spec.id)
        self._append(_UPDATED, spec); return spec

    def get(self, bot_id: str) -> Optional[BotSpec]: return self._state().get(bot_id)
    def list(self) -> List[BotSpec]: return list(self._state().values())

    def delete(self, bot_id: str) -> None:
        if bot_id not in self._state(): raise KeyError(bot_id)
        self.event_store.append(Event(name=_DELETED, payload={"bot_id": bot_id}))

    def pause(self, bot_id: str) -> None:
        if bot_id not in self._state(): raise KeyError(bot_id)
        if bot_id not in self._paused(): self.event_store.append(Event(name=_PAUSED, payload={"bot_id": bot_id}))

    def resume(self, bot_id: str) -> None:
        if bot_id not in self._state(): raise KeyError(bot_id)
        if bot_id in self._paused(): self.event_store.append(Event(name=_RESUMED, payload={"bot_id": bot_id}))

    def is_paused(self, bot_id: str) -> bool:
        return bot_id in self._paused()

    def duplicate(self, bot_id: str, new_id: str) -> BotSpec:
        spec = self.get(bot_id)
        if spec is None: raise KeyError(bot_id)
        if new_id in self._state(): raise ValueError(f"BotSpec already exists: {new_id}")
        copy = replace(spec, id=new_id)
        self._append(_DUPLICATED, copy)
        return copy

    def submit(self, bot_id: str, goal: str, **overrides: Any) -> TaskSpec:
        spec = self.get(bot_id)
        if spec is None: raise KeyError(bot_id)
        if bot_id in self._paused(): raise RuntimeError(f"BotSpec is paused: {bot_id}")
        values = dict(spec.task_defaults); values.update(overrides)
        policy = spec.policy
        # Fail closed at the submission seam: callers cannot widen BotSpec policy.
        requested = set(values.get("required_capabilities", [])) | set(values.get("capabilities", []))
        if requested - set(policy.tools or spec.capabilities):
            raise PermissionError("BotSpec tool/capability policy denied submission")
        if policy.workspace is not None and values.get("workspace_type") not in (None, policy.workspace):
            raise PermissionError("BotSpec workspace policy denied submission")
        if policy.max_cost_usd is not None and float(values.get("max_cost_usd", policy.max_cost_usd)) > policy.max_cost_usd:
            raise PermissionError("BotSpec cost policy denied submission")
        if policy.max_runtime_minutes is not None and int(values.get("max_runtime_minutes", policy.max_runtime_minutes)) > policy.max_runtime_minutes:
            raise PermissionError("BotSpec time policy denied submission")
        if policy.required_grants:
            from hermes.platform.auth.vault import SecretBroker
            for grant in policy.required_grants:
                scope, sep, ref = grant.partition("::")
                if not sep or not SecretBroker().check_grant(scope, ref):
                    raise PermissionError("BotSpec grant policy denied submission")
        values.update(id=values.get("id", f"bot-{uuid.uuid4().hex[:10]}"), title=values.get("title", spec.name), goal=goal)
        return TaskSpec(**values)

    def submit_to_dispatcher(
        self, bot_id: str, routine: str, goal: str, adapter: Any, *,
        idempotency_key: Optional[str] = None, **overrides: Any
    ) -> str:
        """Persist a routine once and return the stable canonical task id.

        The key is recorded in the canonical EventStore event; retries therefore
        reuse the original Kanban task without creating a second ledger entry.
        """
        if idempotency_key is not None:
            for event in self.event_store.get_all(name=_ROUTINE_SUBMITTED):
                payload = event.payload
                if (payload.get("bot_id") == bot_id and payload.get("routine") == routine
                        and payload.get("idempotency_key") == idempotency_key):
                    return str(payload["task_id"])
        task = self.submit_routine(bot_id, routine, goal, idempotency_key=idempotency_key, **overrides)
        # Resolve the complete model/provider binding before persisting: a task
        # with an absent or unknown profile must never enter the dispatcher.
        from hermes.platform.execution.spawn_resolver import SpawnResolver
        SpawnResolver().resolve(task)
        try:
            task_id = adapter.save_task(task)
        except Exception as exc:
            self._record_routine_run(bot_id, routine, task.id, "dispatch_failed", error=str(exc))
            raise
        self._record_routine_run(bot_id, routine, task_id, "submitted")
        return task_id

    def _record_routine_run(self, bot_id: str, routine: str, run_id: str, status: str, **details: Any) -> None:
        """Small internal seam for routine lifecycle events at dispatcher submission."""
        self.record_run(bot_id, routine, run_id, status, **details)

    def list_triggers(self, bot_id: str) -> List[Dict[str, Any]]:
        spec = self.get(bot_id)
        if spec is None:
            raise KeyError(bot_id)
        return [trigger.to_dict() for trigger in spec.triggers]

    def trigger_manual(self, bot_id: str, routine: str, goal: str, **overrides: Any) -> TaskSpec:
        spec = self.get(bot_id)
        if spec is None:
            raise KeyError(bot_id)
        if not any(trigger.type == "manual" for trigger in spec.triggers):
            raise ValueError(f"BotSpec has no manual trigger: {bot_id}")
        return self.submit_routine(bot_id, routine, goal, **overrides)

    def submit_routine(self, bot_id: str, routine: str, goal: str, **overrides: Any) -> TaskSpec:
        idempotency_key = overrides.pop("idempotency_key", None)
        if idempotency_key is not None:
            for event in self.event_store.get_all(name=_ROUTINE_SUBMITTED):
                payload = event.payload
                if (payload.get("bot_id") == bot_id and payload.get("routine") == routine
                        and payload.get("idempotency_key") == idempotency_key):
                    return self.submit(bot_id, payload.get("goal", goal), id=payload["task_id"])
        spec = self.get(bot_id)
        if spec is None: raise KeyError(bot_id)
        definition = spec.routines.get(routine)
        if definition is None: raise KeyError(routine)
        version = int(definition.get("version", 1))
        # Merge antes de expandir: dois `**` com a MESMA chave numa chamada é
        # TypeError em Python, então `**task_defaults, **overrides` estourava
        # sempre que um caller sobrescrevia uma chave que a rotina já define
        # (priority, posture, yolo_mode...). Mesma ordem do submit(): o override
        # do caller vence o default da rotina.
        routine_defaults = dict(definition.get("task_defaults", {}))
        routine_defaults.update(overrides)
        task = self.submit(bot_id, goal, **routine_defaults)
        payload = {"bot_id": bot_id, "routine": routine, "version": version, "task_id": task.id}
        if idempotency_key is not None:
            payload["idempotency_key"] = idempotency_key
            payload["goal"] = goal
        self.event_store.append(Event(name=_ROUTINE_SUBMITTED, payload=payload))
        return task

    def create_cron_job(self, bot_id: str, routine: str, schedule: str, *, prompt: Optional[str] = None, **overrides: Any) -> Dict[str, Any]:
        """Create a bot routine through the canonical cron API."""
        spec = self.get(bot_id)
        if spec is None:
            raise KeyError(bot_id)
        definition = spec.routines.get(routine)
        if definition is None:
            raise KeyError(routine)
        from cron.scheduler import create_job_with_scheduler_registration
        values = dict(definition.get("task_defaults", {}))
        values.update(overrides)
        values.setdefault("prompt", prompt or definition.get("prompt") or routine)
        values.update(schedule=schedule, name=values.get("name", f"{spec.name}: {routine}"), bot_id=bot_id, routine=routine)
        return create_job_with_scheduler_registration(**values)

    def record_run(self, bot_id: str, routine: str, run_id: str, status: str, **details: Any) -> None:
        """Project a dispatcher lifecycle transition into the single EventStore ledger."""
        if self.get(bot_id) is None: raise KeyError(bot_id)
        if status == "dispatch_failed":
            details.setdefault("failure_stage", "dispatch")
        if status not in RUN_STATES and status != "dispatch_failed":
            raise ValueError(f"invalid bot run status: {status!r}")
        # Keep correlation metadata alongside the state; no parallel lifecycle ledger.
        payload = {"bot_id": bot_id, "routine": routine, "run_id": run_id, "status": status, **details}
        self.event_store.append(Event(name=_RUN_RECORDED, payload=payload, correlation_id=str(run_id)))

    def run_state(self, bot_id: str, run_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest canonical lifecycle metadata for a dispatcher run."""
        events = [e.payload for e in self.event_store.get_all()
                  if e.name == _RUN_RECORDED and e.payload.get("bot_id") == bot_id
                  and e.payload.get("run_id") == run_id]
        return events[-1] if events else None

    def run_history(self, bot_id: str, routine: Optional[str] = None) -> List[Dict[str, Any]]:
        return [e.payload for e in self.event_store.get_all() if e.name == _RUN_RECORDED and e.payload.get("bot_id") == bot_id and (routine is None or e.payload.get("routine") == routine)]

    def _append(self, name: str, spec: BotSpec) -> None:
        self.event_store.append(Event(name=name, payload={"bot_id": spec.id, "spec": spec.to_dict()}))
