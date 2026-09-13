"""Emendas 4 e 8/9 — evento de rota esgotada + required/preferred (invariantes).

Contratos de comportamento:
(a) ExactModelRouter emite o snapshot de domínio ANTES de levantar
    ModelRouteExhaustedException; listener recebe família/variant e a lista de
    providers esgotados; unsubscribe remove o listener.
(b) Evento de domínio provider.route_exhausted carrega o payload estável.
(c) resolve_model_profile: required > posture > preferred > unbound (o soft
    NUNCA sobrescreve required/postura).
(d) agent_eligibility/select_agent_candidate: required é allow-list (fail-fast),
    preferred só ordena elegíveis.
(e) Persistência round-trip no KanbanAdapter + guard no claim_tick
    (agent_required_missing com orçamento) + SpawnResolver falha rápido.
"""

import os
import tempfile
import unittest
from pathlib import Path

from hermes.platform.models.profiles import (
    ModelIdentity, ModelProfile, ProviderRoute,
)
from hermes.platform.models.circuit_breaker import CircuitBreaker
from hermes.platform.models.provider_router import (
    ExactModelRouter, ModelRouteExhaustedException,
    subscribe_route_exhausted, unsubscribe_route_exhausted,
)
from hermes.platform.observability.events import (
    Event, ROUTE_EXHAUSTED, route_exhausted_event,
)
from hermes.platform.execution.preferences import (
    agent_eligibility, resolve_model_profile, select_agent_candidate,
)
from hermes.platform.execution.spawn_resolver import (
    SpawnResolver, AgentRequirementError,
)
from hermes.platform.execution.dispatcher import HAOSDispatcher
from hermes.platform.execution.lane_executor import DeterministicLaneWorker
from hermes.platform.tasks.spec import TaskSpec
from hermes.platform.tasks.kanban_adapter import KanbanAdapter


class TestRouteExhaustedEvent(unittest.TestCase):
    def test_router_emits_snapshot_before_raising(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        profile = ModelProfile(
            id="p", model_identity=ModelIdentity("m", "v"),
            routes=[ProviderRoute("a", "ma"), ProviderRoute("b", "mb")],
        )
        received = []
        subscribe_route_exhausted(received.append)
        try:
            router = ExactModelRouter(circuit_breaker=cb)
            for provider in ("a", "b"):
                key = cb.route_key(provider, profile.model_identity)
                cb.record_failure(key)  # threshold 1 => abre na hora
            with self.assertRaises(ModelRouteExhaustedException):
                router.select_route(profile)
        finally:
            unsubscribe_route_exhausted(received.append)

        self.assertEqual(len(received), 1)
        snapshot = received[0]
        self.assertEqual(snapshot["model_family"], "m")
        self.assertEqual(snapshot["model_variant"], "v")
        self.assertEqual(snapshot["exhausted_providers"], ["a", "b"])
        self.assertEqual(snapshot["provider_chain"], ["a", "b"])

    def test_domain_event_payload_is_stable(self):
        evt = route_exhausted_event(
            model_family="deepseek-v4", model_variant="flash",
            provider_chain=["a", "b"], exhausted_providers=["a", "b"],
        )
        self.assertIsInstance(evt, Event)
        self.assertEqual(evt.name, ROUTE_EXHAUSTED)
        self.assertEqual(evt.payload["model_family"], "deepseek-v4")
        self.assertEqual(evt.payload["exhausted_providers"], ["a", "b"])

    def test_unsubscribe_removes_listener(self):
        profile = ModelProfile(
            id="p", model_identity=ModelIdentity("m", "v"),
            routes=[ProviderRoute("a", "ma")],
        )
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        key = cb.route_key("a", profile.model_identity)
        cb.record_failure(key)
        received = []
        subscribe_route_exhausted(received.append)
        unsubscribe_route_exhausted(received.append)
        router = ExactModelRouter(circuit_breaker=cb)
        with self.assertRaises(ModelRouteExhaustedException):
            router.select_route(profile)
        self.assertEqual(received, [])


class TestRequiredPreferredModel(unittest.TestCase):
    def test_model_precedence_required_posture_preferred_unbound(self):
        self.assertEqual(resolve_model_profile("req", "post", "pref"),
                         ("req", "required"))
        self.assertEqual(resolve_model_profile(None, "post", "pref"),
                         ("post", "posture"))      # postura vence o soft
        self.assertEqual(resolve_model_profile(None, None, "pref"),
                         ("pref", "preferred"))    # soft só sem binding
        self.assertEqual(resolve_model_profile(None, None, None),
                         (None, "unbound"))

    def test_agent_eligibility_and_selection(self):
        self.assertEqual(agent_eligibility([], [], "hermes"), (True, False))
        self.assertEqual(agent_eligibility(["kilo"], [], "kilo"), (True, False))
        self.assertEqual(agent_eligibility(["kilo"], [], "hermes"), (False, False))
        self.assertEqual(
            agent_eligibility(["kilo"], ["kilo"], "kilo"), (True, True)
        )
        # required é allow-list; preferred só ordena.
        self.assertEqual(
            select_agent_candidate([], ["kilo"], ["hermes", "kilo"]), "kilo"
        )
        self.assertEqual(
            select_agent_candidate(["kilo"], [], ["hermes", "kilo"]), "kilo"
        )
        self.assertIsNone(
            select_agent_candidate(["kilo"], [], ["hermes", "vision"])
        )

    def test_task_spec_defaults_additive(self):
        t = TaskSpec(id="T-1", title="t", goal="g")
        self.assertEqual(t.required_agents, [])
        self.assertEqual(t.preferred_agents, [])
        self.assertIsNone(t.model_profile_preferred)
        t2 = TaskSpec(id="T-2", title="t", goal="g",
                      required_agents=["kilo"], preferred_agents=["kilo"],
                      model_profile_preferred="review-primary")
        self.assertEqual(t2.required_agents, ["kilo"])


class TestRequiredPreferredWiring(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env_kanban = os.environ.get("HERMES_KANBAN_HOME")
        self._env_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_KANBAN_HOME"] = str(root / "kanban-home")
        os.environ["HERMES_HOME"] = str(root / "hermes-home")
        self.db = root / "kanban.db"
        self.adapter = KanbanAdapter(self.db)

    def tearDown(self):
        self.adapter.close()
        if self._env_kanban is None:
            os.environ.pop("HERMES_KANBAN_HOME", None)
        else:
            os.environ["HERMES_KANBAN_HOME"] = self._env_kanban
        if self._env_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._env_home
        self._tmp.cleanup()

    def test_spawn_resolver_required_agents_fail_fast(self):
        t = TaskSpec(id="T-A", title="t", goal="g",
                     required_agents=["kilo"], workspace_type="scratch")
        # scratch + sem caps git -> lane hermes, fora do required.
        with self.assertRaises(AgentRequirementError):
            SpawnResolver().resolve(t)

    def test_spawn_resolver_soft_preferred_never_overrides_posture(self):
        resolver = SpawnResolver()
        # Postura implementer liga coding-primary; preferred não muda nada.
        t = TaskSpec(id="T-S", title="t", goal="g",
                     model_profile_preferred="architecture-primary")
        a = resolver.resolve(t)
        self.assertEqual(a.model_profile_id, "coding-primary")
        self.assertEqual(a.binding_level, "posture")
        # Explicit required vence e marca o nível.
        t2 = TaskSpec(id="T-R", title="t", goal="g",
                      model_profile="review-primary",
                      model_profile_preferred="coding-primary")
        a2 = resolver.resolve(t2)
        self.assertEqual(a2.model_profile_id, "review-primary")
        self.assertEqual(a2.binding_level, "required")

    def test_claim_tick_agent_required_missing_with_budget(self):
        spec = TaskSpec(id="T-G1", title="Guarded", goal="g",
                        workspace_type="scratch",
                        required_agents=["kilo"])
        tid = self.adapter.save_task(spec, status="READY")
        dispatcher = HAOSDispatcher(self.adapter,
                                    lane_worker=DeterministicLaneWorker())
        executed = dispatcher.claim_tick(max_spawn=1)
        self.assertEqual(executed, [])
        # claim liberado; run encerrado com exit_reason específico.
        self.assertEqual(self.adapter.get_task(tid)["status"], "ready")
        run = self.adapter.get_run(tid)
        self.assertEqual(run.status, "ended")
        self.assertEqual(run.exit_reason, "agent_required_missing")
        # Segundo tick: orçamento estoura -> blocked.
        dispatcher.claim_tick(max_spawn=1)
        self.assertEqual(self.adapter.get_task(tid)["status"], "blocked")

    def test_spec_json_round_trip_persists_agents_and_preferred(self):
        spec = TaskSpec(id="T-P", title="P", goal="g",
                        required_agents=["kilo"], preferred_agents=["kilo"],
                        model_profile_preferred="coding-primary")
        tid = self.adapter.save_task(spec, status="READY")
        persisted = self.adapter.get_task(tid)["spec"]
        self.assertEqual(persisted["required_agents"], ["kilo"])
        self.assertEqual(persisted["preferred_agents"], ["kilo"])
        self.assertEqual(persisted["model_profile_preferred"], "coding-primary")

    def test_team_binding_for_soft_preferred(self):
        from hermes.platform.execution.team import (
            TeamSpec, TeamRole, TeamResolver,
        )
        resolver = TeamResolver()
        # binding_for não exige registro: resolve direto do roster (papel sem
        # tabela canônica p/ isolar o caminho soft).
        naked = TeamSpec(
            team_id="pref-team", name="pref",
            roles=[TeamRole(role_id="naked")],
            gate_edges=(),
        )
        # Sem required/tabela => preferred (soft) resolve e marca o nível.
        binding = resolver.binding_for(
            naked, "naked",
            task_model_profile_preferred="review-primary",
        )
        self.assertEqual(binding["model_profile"], "review-primary")
        self.assertEqual(binding["binding_level"], "preferred")
        # Required da task vence o preferred.
        binding2 = resolver.binding_for(
            naked, "naked",
            task_model_profile="coding-primary",
            task_model_profile_preferred="review-primary",
        )
        self.assertEqual(binding2["model_profile"], "coding-primary")
        self.assertEqual(binding2["binding_level"], "required")
        # Papel canônico (tabela) também vence o preferred (default domina).
        canon = TeamSpec(
            team_id="canon-team", name="canon",
            roles=[TeamRole(role_id="polecat")],
            gate_edges=(),
        )
        binding3 = resolver.binding_for(
            canon, "polecat",
            task_model_profile_preferred="security-primary",
        )
        self.assertEqual(binding3["model_profile"], "coding-primary")
        self.assertEqual(binding3["binding_level"], "required")


if __name__ == "__main__":
    unittest.main()
