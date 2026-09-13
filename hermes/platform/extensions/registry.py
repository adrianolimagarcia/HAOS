"""HAOS Extension Fabric (v1.1 Emenda 23/24) + port clean-room DSH/cordis (Fase 1).

A thin meta-layer *above* the existing Hermes PluginManager, not a second
plugin runtime. An extension declares a manifest (what capabilities/tools it
provides, what it requires) and flows through an explicit lifecycle:

    PENDING -> LOADING -> ACTIVE -> UNLOADING -> DISPOSED
                              ↳ FAILED                   (activation error)
         PENDING|ACTIVE|FAILED -> STOPPED (non-terminal, restartable)

When activated, provided capabilities are bridged into the platform
CapabilityRegistry so other extensions declare dependencies by capability id
("requires: capability:lsp") instead of importing code.

Fase-1 additions (clean-room mirror of the cordis FiberState machine and the
DynamicCordisRunnerService semantics audited in TEMP/references/deepseek-harness
vendor/cordis + packages/extensions/cordis-host-runner; concepts only, no code
copied):

* **FAILED/STOPPED** states + per-id **state history** + **lifecycle events**
  (ExtensionEvent + subscribe()) — cordis emits internal/status on every
  transition and forbids restart after DISPOSED (fiber.ts:147-154, 351-354).
* **activate_with_deps(id, on_missing="raise"|"hold")**: cordis parks a fiber
  whose required services are missing and auto-promotes it when a provider
  appears (fiber _refresh + reflect.notify; composition.spec.ts "consumer
  first"). ``activate()`` keeps raising (API-compatible); hold mode parks the
  extension with a ``waiting_for`` list and auto-activates it once an ACTIVE
  extension bridges the missing capability (or ``retry_waiting()`` is called).
* **Rollback on partial activation**: bridge + load callback run in try/finally
  — on failure the capabilities bridged by THIS call are un-bridged, the state
  goes FAILED with a diagnostic, and the error is re-raised (lifecycle.ts
  "never leave a failed fiber mounted").
* **deactivate(id, cascade=False)**: dependent-first ordering — ACTIVE
  dependents (extensions whose ``requires`` lists a capability this one
  provides) are deactivated before the provider (reflect.ts:297-303). Without
  ``cascade=True`` a provider with live dependents refuses to deactivate.
* **stop(id)** non-terminal (cordis stop analog) + per-id **in-flight guard**
  and RLock against concurrent double activation.
* **CapabilityRegistry.register** unions providers (a second ACTIVE provider of
  the same capability joins instead of erasing the first) and un-bridging
  removes only this extension's provider.

Rules kept from Fase 0: this layer NEVER imports/executes a plugin runtime —
real Hermes plugin load/grant/order live upstream (hermes_cli/plugins_*); the
registry only records declarations, gates capability requires, and bridges
declared capabilities additively.
"""

from dataclasses import dataclass, field
from threading import RLock
from time import time
from typing import Callable, Dict, Any, List, Optional

from hermes.platform.capabilities.registry import Capability, CapabilityRegistry

# Lifecycle states (Cordis/DSH-inspired, per HAOS Emenda 22).
PENDING = "PENDING"
LOADING = "LOADING"
ACTIVE = "ACTIVE"
FAILED = "FAILED"
STOPPED = "STOPPED"
UNLOADING = "UNLOADING"
DISPOSED = "DISPOSED"

LIFECYCLE = (PENDING, LOADING, ACTIVE, FAILED, STOPPED, UNLOADING, DISPOSED)

# Terminal state: a disposed fiber cannot restart (cordis assertActive).
_TERMINAL = frozenset({DISPOSED})
# States from which (re)activation is legal.
_RESTARTABLE_FROM = frozenset({PENDING, FAILED, STOPPED})
# States an active fiber may be stopped from.
_STOPPABLE_FROM = frozenset({PENDING, LOADING, ACTIVE, FAILED})

_Listener = Callable[["ExtensionEvent"], None]


@dataclass(frozen=True)
class ExtensionEvent:
    """Lifecycle transition event (cordis internal/status analog)."""

    extension_id: str
    from_state: Optional[str]
    to_state: str
    ts: float = field(default_factory=time)
    detail: Optional[str] = None


@dataclass
class ExtensionManifest:
    id: str
    version: str
    description: str = ""
    provides: List[str] = field(default_factory=list)      # "capability:x" | "tool:y"
    requires: List[str] = field(default_factory=list)      # "service:z" | "capability:lsp"
    optional: List[str] = field(default_factory=list)
    subscribes_events: List[str] = field(default_factory=list)
    publishes_events: List[str] = field(default_factory=list)
    permissions: Dict[str, Any] = field(default_factory=dict)  # filesystem/network scope
    ui: Dict[str, bool] = field(default_factory=dict)          # dashboard/desktop

PluginManifest = ExtensionManifest  # Canonical ADR-002 alias


class ExtensionRegistry:
    """Registry + lifecycle manager + capability bridge for HAOS extensions."""

    def __init__(
        self,
        capability_registry: Optional[CapabilityRegistry] = None,
        *,
        load_callback: Optional[Callable[[ExtensionManifest], None]] = None,
    ):
        self._manifests: Dict[str, ExtensionManifest] = {}
        self._state: Dict[str, str] = {}
        self._provided_capabilities: Dict[str, List[str]] = {}
        self.capability_registry = capability_registry  # optional CapabilityRegistry
        # Fase 1 (DSH/cordis port): lifecycle events, state history, waiters.
        self._lock = RLock()
        self._listeners: List[_Listener] = []
        self._history: Dict[str, List[Dict[str, Any]]] = {}
        self._in_flight = set()
        self._waiters: Dict[str, List[str]] = {}        # missing cap id -> [ext ids]
        self._waiting_for: Dict[str, List[str]] = {}    # ext id -> [missing cap ids]
        self._failures: Dict[str, Dict[str, Any]] = {}  # ext id -> {phase, message}
        self._load_callback = load_callback             # activation body (LOADING)

    # -- registration / manifest -------------------------------------------
    def register(self, manifest: ExtensionManifest):
        with self._lock:
            if manifest.id in self._manifests:
                raise ValueError(f"Extension '{manifest.id}' already registered")
            self._manifests[manifest.id] = manifest
            self._state[manifest.id] = PENDING
            self._history[manifest.id] = [{"from": None, "to": PENDING, "ts": time()}]

    def get(self, extension_id: str) -> Optional[ExtensionManifest]:
        return self._manifests.get(extension_id)

    def status(self, extension_id: str) -> Optional[str]:
        return self._state.get(extension_id)

    def list_manifest_ids(self) -> List[str]:
        return sorted(self._manifests)

    # -- lifecycle events + history (Fase 1) --------------------------------
    def subscribe(self, listener: _Listener) -> None:
        """Registra um listener de ExtensionEvent (transições de estado).

        Listeners não devem lançar exceções: o lifecycle não engole falhas de
        observador (determinismo nos testes; evite efeitos colaterais aqui).
        """
        self._listeners.append(listener)

    def emit(self, event: ExtensionEvent) -> None:
        for listener in list(self._listeners):
            listener(event)

    def state_history(self, extension_id: str) -> List[Dict[str, Any]]:
        """Transições registradas do papel (append-only; sem overwrite)."""
        with self._lock:
            return [dict(entry) for entry in self._history.get(extension_id, [])]

    def last_failure(self, extension_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            failure = self._failures.get(extension_id)
            return dict(failure) if failure is not None else None

    def waiting_for(self, extension_id: str) -> List[str]:
        """Capacidades requeridas ainda ausentes (parked em hold-mode)."""
        with self._lock:
            return list(self._waiting_for.get(extension_id, []))

    # -- lifecycle ----------------------------------------------------------
    def activate(self, extension_id: str) -> str:
        """Ativa a extensão; levanta RuntimeError se um requisito `capability:`
        não é satisfeito por nenhuma extensão ACTIVE (comportamento Fase 0)."""
        return self._do_activate(extension_id, on_missing="raise")

    def activate_with_deps(self, extension_id: str, *, on_missing: str = "raise") -> str:
        """Ativa com política de dependência (port cordis).

        ``on_missing="raise"`` (default): como ``activate()``, levanta se falta
        capacidade requerida. ``on_missing="hold"``: estaciona a extensão em
        PENDING registrando ``waiting_for``; a primeira ativação de uma
        extensão que bridga a capacidade ausente promove automaticamente as
        estacionadas (ou chame ``retry_waiting()``).
        """
        return self._do_activate(extension_id, on_missing=on_missing)

    def _do_activate(self, extension_id: str, *, on_missing: str) -> str:
        manifest = self._require(extension_id)
        if on_missing not in ("raise", "hold"):
            raise ValueError(f"on_missing must be 'raise'|'hold', got '{on_missing}'")
        with self._lock:
            current = self._state.get(extension_id)
            if current == ACTIVE:
                return ACTIVE
            if current in _TERMINAL:
                raise RuntimeError(
                    f"Cannot activate '{extension_id}': state '{current}' is terminal "
                    f"(DISPOSED fibers cannot restart)."
                )
            if current not in _RESTARTABLE_FROM:
                raise RuntimeError(
                    f"Cannot activate '{extension_id}' from state '{current}'."
                )
            if extension_id in self._in_flight:
                raise RuntimeError(f"Transition in-flight for '{extension_id}'.")
            self._in_flight.add(extension_id)
        try:
            missing = self._unsatisfied_requirements(manifest)
            if missing:
                if on_missing == "hold":
                    with self._lock:
                        self._record_waiting(extension_id, missing)
                    return PENDING  # parked; auto-promoted when a provider appears
                raise RuntimeError(
                    f"Cannot activate '{extension_id}': required capability "
                    f"{missing[0] if len(missing) == 1 else missing} is not provided "
                    f"by any ACTIVE extension."
                )
            self._transition(extension_id, LOADING, detail="activation start")
            try:
                self._bridge_capabilities(manifest)
                if self._load_callback is not None:
                    self._load_callback(manifest)
            except Exception as exc:
                # Rollback: desbridga só o que ESTA ativação bridgou; nunca
                # deixa a extensão ACTIVE/LOADING com efeitos parciais.
                self._unbridge(extension_id)
                self._record_failure(extension_id, phase="load", message=str(exc))
                self._transition(extension_id, FAILED, detail=f"load failed: {exc}")
                raise RuntimeError(
                    f"Activation of '{extension_id}' failed during load: {exc}"
                ) from exc
            self._transition(extension_id, ACTIVE, detail="activation ok")
            # Extensão ativa não está mais em waiting; e auto-promoção de
            # estacionadas que esperavam por esta capability.
            self._clear_waiting(extension_id)
            self._promote_waiters_for(extension_id, manifest)
            return ACTIVE
        finally:
            with self._lock:
                self._in_flight.discard(extension_id)

    def retry_waiting(self) -> List[str]:
        """Re-tenta todas as extensões estacionadas (hold-mode).

        Útil após registrar uma capability no CapabilityRegistry sem passar
        por outra extensão ACTIVE. Retorna os ids que saíram de waiting
        (ACTIVE ou FAILED)."""
        with self._lock:
            parked = [
                ext_id
                for ext_id, missing in self._waiting_for.items()
                if missing
            ]
        for ext_id in parked:
            self._do_activate(ext_id, on_missing="hold")
        with self._lock:
            return [e for e in parked if not self._waiting_for.get(e)]

    def stop(self, extension_id: str) -> str:
        """Parada não-terminal (cordis stop analog): manifest/registro sobrevivem.

        Estado vai a STOPPED e as capabilities bridgadas são desligadas; uma
        nova ``activate()`` re-bridga. DISPOSED não pode ser reiniciado."""
        manifest = self._require(extension_id)
        with self._lock:
            current = self._state.get(extension_id)
            if current == DISPOSED:
                return DISPOSED
            if current not in _STOPPABLE_FROM:
                raise RuntimeError(f"Cannot stop '{extension_id}' from state '{current}'.")
        self._transition(extension_id, UNLOADING, detail="stop: unloading")
        self._unbridge(extension_id)
        self._clear_waiting(extension_id)
        self._transition(extension_id, STOPPED, detail="stopped")
        return STOPPED

    def deactivate(self, extension_id: str, *, cascade: bool = False) -> str:
        """Desativa (terminal -> DISPOSED), dependentes primeiro.

        ``cascade=True`` desativa primeiro os dependentes ACTIVE (extensões
        cujo ``requires`` lista uma capability fornecida por esta); sem
        cascade, um provider com dependentes ACTIVE se recusa a desativar."""
        manifest = self._require(extension_id)
        with self._lock:
            if self._state.get(extension_id) == DISPOSED:
                return DISPOSED
            dependents = self._dependents(extension_id)
            if dependents and not cascade:
                raise RuntimeError(
                    f"Cannot deactivate '{extension_id}': ACTIVE dependents "
                    f"{sorted(dependents)}. Use cascade=True (dependent-first)."
                )
            for dependent in dependents:  # drenar dependentes antes do provider
                self.deactivate(dependent, cascade=True)
        self._transition(extension_id, UNLOADING, detail="deactivate: unloading")
        self._unbridge(extension_id)
        self._clear_waiting(extension_id)
        self._transition(extension_id, DISPOSED, detail="disposed")
        return DISPOSED

    # -- internals ----------------------------------------------------------
    def _require(self, extension_id: str) -> ExtensionManifest:
        manifest = self._manifests.get(extension_id)
        if manifest is None:
            raise KeyError(f"Unknown extension '{extension_id}'")
        return manifest

    def _transition(self, extension_id: str, to_state: str, detail: Optional[str] = None) -> None:
        with self._lock:
            prev = self._state.get(extension_id)
            if prev == to_state:
                return
            self._state[extension_id] = to_state
            self._history.setdefault(extension_id, []).append(
                {"from": prev, "to": to_state, "ts": time(), "detail": detail}
            )
            event = ExtensionEvent(
                extension_id=extension_id, from_state=prev, to_state=to_state,
                detail=detail,
            )
        self.emit(event)

    def _unsatisfied_requirements(self, manifest: ExtensionManifest) -> List[str]:
        """Requisitos `capability:` ainda insatisfeitos (gate Fase 0).

        Prefixos fora do vocabulário (ex.: ``service:``) são apenas
        registrados no history via detail — este meta-layer não gate serviços;
        capacidades vêm de extensões ACTIVE ou do CapabilityRegistry."""
        missing = []
        for requirement in manifest.requires:
            if requirement.startswith("capability:"):
                cap_id = requirement.split(":", 1)[1]
                if not self._capability_satisfied(cap_id):
                    missing.append(cap_id)
        return missing

    def _capability_satisfied(self, cap_id: str) -> bool:
        if self.capability_registry is not None and self.capability_registry.get(cap_id):
            return True
        return any(
            cap_id in caps
            for ext_id, caps in self._provided_capabilities.items()
            if self._state.get(ext_id) == ACTIVE
        )

    def _bridge_capabilities(self, manifest: ExtensionManifest):
        provided = []
        for item in manifest.provides:
            if item.startswith("capability:"):
                cap_id = item.split(":", 1)[1]
                provided.append(cap_id)
                if self.capability_registry is not None:
                    existing = self.capability_registry.get(cap_id)
                    if existing is None:
                        self.capability_registry.register(
                            Capability(id=cap_id, execution_kind="agentic", providers=[manifest.id])
                        )
                    else:
                        # Union aditiva: um segundo provider ACTIVE junta-se em
                        # vez de apagar o primeiro (port cordis dup-provide).
                        self.capability_registry.add_provider(cap_id, manifest.id)
        self._provided_capabilities[manifest.id] = provided

    def _unbridge(self, extension_id: str) -> None:
        """Remove só as capabilities bridgadas por esta extensão."""
        provided = self._provided_capabilities.pop(extension_id, [])
        if self.capability_registry is not None:
            for cap_id in provided:
                self.capability_registry.remove_provider(cap_id, extension_id)

    def _dependents(self, extension_id: str) -> List[str]:
        """Extensões ACTIVE cujo requires lista capability fornecida por esta."""
        provided = set(self._provided_capabilities.get(extension_id, []))
        if not provided:
            return []
        dependents = []
        for ext_id, manifest in self._manifests.items():
            if ext_id == extension_id or self._state.get(ext_id) != ACTIVE:
                continue
            needs = {
                req.split(":", 1)[1]
                for req in manifest.requires
                if req.startswith("capability:")
            }
            if needs & provided:
                dependents.append(ext_id)
        return dependents

    def _record_waiting(self, extension_id: str, missing: List[str]) -> None:
        self._clear_waiting(extension_id)  # re-estacionar nunca duplica
        self._waiting_for[extension_id] = list(missing)
        for cap_id in missing:
            parked = self._waiters.setdefault(cap_id, [])
            if extension_id not in parked:
                parked.append(extension_id)

    def _clear_waiting(self, extension_id: str) -> None:
        self._waiting_for.pop(extension_id, None)
        for parked in self._waiters.values():
            if extension_id in parked:
                parked.remove(extension_id)

    def _record_failure(self, extension_id: str, *, phase: str, message: str) -> None:
        with self._lock:
            attempt = self._failures.get(extension_id, {}).get("attempt", 0) + 1
            self._failures[extension_id] = {
                "phase": phase, "message": message, "attempt": attempt,
            }

    def _promote_waiters_for(self, provider_id: str, manifest: ExtensionManifest) -> None:
        """Auto-ativa estacionadas que esperavam por capability bridgada agora."""
        with self._lock:
            candidates = set()
            for item in manifest.provides:
                if item.startswith("capability:"):
                    cap_id = item.split(":", 1)[1]
                    candidates.update(self._waiters.get(cap_id, []))
            candidates.discard(provider_id)
            parked = sorted(candidates)
        for ext_id in parked:
            self._do_activate(ext_id, on_missing="hold")
