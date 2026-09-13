"""TeamSpec (port GasTown, Fase 1) — testes invariantes.

Contracts (relações entre dados, nunca snapshots de valores):
1. Time default ≡ conjunto canônico de papéis.
2. ROLE_*_MAP cobrem exatamente os papéis canônicos (sem drift entre tabelas).
3. Toda postura mapeada existe no PostureResolver real (introspecção).
4. Todo perfil de modelo efetivo resolve no ModelResolver real (config JSON).
5. Precedência de modelo: task > role override > tabela canônica do papel.
6. Regras de composição (refinery/polecat -> witness; dog -> deacon); mayor é
   opcional em time mínimo de rig (fiel ao GasTown).
7. DAG de gates acíclico; ciclo sintético levanta TeamValidationError.
8. Cardinalidade: sem role_id duplicado; multiplicity explícita conflitante
   com a canônica levanta.
"""

import unittest

from hermes.platform.execution.team import (
    TeamRole,
    TeamSpec,
    TeamResolver,
    TeamValidationError,
    CANONICAL_ROLE_IDS,
    COMPOSITION_RULES,
    DEFAULT_TEAM_ID,
    ROLE_POSTURE_MAP,
    ROLE_MODEL_MAP,
    ROLE_LANE_MAP,
    default_team,
    register_default_team,
)
from hermes.platform.posture.specs import PostureResolver
from hermes.platform.models.model_resolver import ModelResolver


class TestTeamSpecInvariants(unittest.TestCase):
    def test_default_team_has_all_canonical_roles(self):
        team = default_team()
        self.assertEqual(
            {r.role_id for r in team.roles}, set(CANONICAL_ROLE_IDS)
        )
        # Papel one-multiplicity canônico aparece no máximo uma vez no default.
        one = [r.role_id for r in team.roles if r.multiplicity == "one"]
        self.assertEqual(len(one), len(set(one)))

    def test_role_maps_cover_exactly_canonical_roles(self):
        # As três tabelas canônicas não podem divergir do conjunto de papéis.
        self.assertEqual(set(ROLE_POSTURE_MAP), set(CANONICAL_ROLE_IDS))
        self.assertEqual(set(ROLE_MODEL_MAP), set(CANONICAL_ROLE_IDS))
        self.assertEqual(set(ROLE_LANE_MAP), set(CANONICAL_ROLE_IDS))

    def test_every_mapped_posture_exists_in_resolver(self):
        # Introspecta o PostureResolver real: relação, não lista hardcoded.
        resolver = PostureResolver()
        registered = set(resolver.registered_ids())
        for posture_id in ROLE_POSTURE_MAP.values():
            self.assertIn(posture_id, registered)

    def test_every_default_role_model_profile_resolves(self):
        model_resolver = ModelResolver()
        team = default_team()
        for role in team.roles:
            profile = role.model_profile or ROLE_MODEL_MAP[role.role_id]
            self.assertIsNotNone(model_resolver.resolve(profile))  # levanta se desconhecido

    def test_model_precedence_task_role_table(self):
        resolver = TeamResolver()
        team = default_team()
        base = resolver.binding_for(team, "polecat")
        self.assertEqual(base["model_profile"], "coding-primary")

        # Override do TeamRole vence a tabela canônica (time com witness p/ a
        # regra de composição polecat=>witness valer).
        custom = TeamSpec(
            team_id="custom", name="Custom",
            roles=[
                TeamRole(role_id="witness"),
                TeamRole(role_id="polecat", model_profile="security-primary"),
            ],
            gate_edges=(("witness", "polecat"),),
        )
        resolver.validate(custom)
        self.assertEqual(
            resolver.binding_for(custom, "polecat")["model_profile"], "security-primary"
        )
        # Binding explícito da task vence o override do papel.
        self.assertEqual(
            resolver.binding_for(custom, "polecat", task_model_profile="orchestrator-primary")[
                "model_profile"
            ],
            "orchestrator-primary",
        )

    def test_composition_rules_refinery_polecat_dog(self):
        resolver = TeamResolver()
        # refinery sem witness -> inválido.
        bad = TeamSpec(
            team_id="bad-refinery", name="x",
            roles=[TeamRole(role_id="refinery"), TeamRole(role_id="mayor")],
            gate_edges=(),
        )
        with self.assertRaises(TeamValidationError):
            resolver.validate(bad)
        # polecat sem witness -> inválido.
        bad2 = TeamSpec(
            team_id="bad-polecat", name="x",
            roles=[TeamRole(role_id="polecat"), TeamRole(role_id="mayor")],
            gate_edges=(),
        )
        with self.assertRaises(TeamValidationError):
            resolver.validate(bad2)
        # dog sem deacon -> inválido.
        bad3 = TeamSpec(
            team_id="bad-dog", name="x",
            roles=[TeamRole(role_id="dog"), TeamRole(role_id="mayor")],
            gate_edges=(),
        )
        with self.assertRaises(TeamValidationError):
            resolver.validate(bad3)
        # Time mínimo de rig (witness + refinery, sem mayor) é VÁLIDO — o
        # GasTown não exige mayor em patrulha mínima.
        rig = TeamSpec(
            team_id="min-rig", name="x",
            scope="rig",
            roles=[TeamRole(role_id="witness"), TeamRole(role_id="refinery")],
            gate_edges=(("witness", "refinery"),),
        )
        resolver.validate(rig)

    def test_gate_edges_must_form_dag(self):
        resolver = TeamResolver()
        # Default (mayor->polecat->witness->refinery) valida.
        resolver.validate(default_team())
        # Ciclo sintético -> inválido.
        cycled = TeamSpec(
            team_id="cycle", name="x",
            roles=[
                TeamRole(role_id="witness"), TeamRole(role_id="refinery"),
                TeamRole(role_id="polecat"), TeamRole(role_id="mayor"),
            ],
            gate_edges=(("mayor", "polecat"), ("polecat", "witness"),
                        ("witness", "refinery"), ("refinery", "mayor")),
        )
        with self.assertRaises(TeamValidationError):
            resolver.validate(cycled)
        # Edge com endpoint fora do roster -> inválido.
        dangling = TeamSpec(
            team_id="dangling", name="x",
            roles=[TeamRole(role_id="mayor")],
            gate_edges=(("mayor", "polecat"),),
        )
        with self.assertRaises(TeamValidationError):
            resolver.validate(dangling)

    def test_no_duplicate_roles_and_multiplicity_matches_canonical(self):
        resolver = TeamResolver()
        duplicate = TeamSpec(
            team_id="dup", name="x",
            roles=[TeamRole(role_id="witness"), TeamRole(role_id="witness")],
            gate_edges=(),
        )
        with self.assertRaises(TeamValidationError):
            resolver.validate(duplicate)
        # Declarar multiplicity "many" num papel one -> inválido.
        wrong_multiplicity = TeamSpec(
            team_id="wrong-mult", name="x",
            roles=[TeamRole(role_id="witness", multiplicity="many")],
            gate_edges=(),
        )
        with self.assertRaises(TeamValidationError):
            resolver.validate(wrong_multiplicity)
        # Papel desconhecido -> inválido.
        unknown = TeamSpec(
            team_id="unknown-role", name="x",
            roles=[TeamRole(role_id="bard")],
            gate_edges=(),
        )
        with self.assertRaises(TeamValidationError):
            resolver.validate(unknown)

    def test_resolver_register_resolve_and_unknown(self):
        resolver = TeamResolver()
        register_default_team(resolver)
        team = resolver.resolve(DEFAULT_TEAM_ID)
        self.assertTrue(team.default_team)
        with self.assertRaises(TeamValidationError):
            resolver.resolve("no-such-team")


if __name__ == "__main__":
    unittest.main()
