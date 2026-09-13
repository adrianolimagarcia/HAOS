"""Contratos tipados de I/O (Fase 1, padrão Agno) — invariantes.

Contratos de comportamento entre dados, nunca snapshots: presença obrigatória,
checagem estrita de tipo, enum, política de chaves extras, profundidade JSON,
semântica HARD (raise) vs SOFT (anotar residual_risk/evidence), aditividade do
campo ``TaskSpec.task_contract`` e integração com o dict da LaneWorker.
"""

import json
import tempfile
import unittest
from pathlib import Path

from hermes.platform.execution.contracts import (
    FieldContract, TaskIOContract, ContractGuard, ContractViolationError,
    ContractViolationKind, validate_contract, empty_contract,
)
from hermes.platform.execution.lane_executor import DeterministicLaneWorker
from hermes.platform.tasks.spec import TaskSpec


class TestFieldValidation(unittest.TestCase):
    def test_required_field_validates_and_missing_required_violates(self):
        fields = [FieldContract("title", "str", required=True)]
        self.assertEqual(validate_contract({"title": "x"}, fields), [])
        violations = validate_contract({}, fields)
        self.assertEqual(len(violations), 1)
        self.assertTrue(violations[0].startswith("missing_required:"))

    def test_type_mismatch_detected_for_each_supported_type(self):
        # (tipo, valor conforme, valor violador)
        cases = [
            ("str", "ok", 5),
            ("int", 7, "x"),
            ("int", 7, True),   # bool NÃO é int
            ("float", 1.5, "x"),
            ("float", 1.5, 2),  # int não é aceito como float (estrito)
            ("bool", True, 1),
            ("path", "a/b", ""),
            ("artifact_ref", "artifact://a1", ""),
        ]
        for ftype, good, bad in cases:
            f = FieldContract("v", ftype, required=True)
            self.assertEqual(validate_contract({"v": good}, [f]), [], ftype)
            violations = validate_contract({"v": bad}, [f])
            self.assertTrue(
                any(v.startswith("type_mismatch:") for v in violations), ftype
            )

        # json é valor livre: payload inválido vira json_invalid (não
        # type_mismatch) — coberto na checagem recursiva dedicada.
        fj = FieldContract("v", "json", required=True)
        violations = validate_contract({"v": {"a": b"bytes"}}, [fj])
        self.assertTrue(any(v.startswith("json_invalid:") for v in violations))

    def test_enum_enforced(self):
        f = FieldContract("level", "str", enum=("low", "high"))
        self.assertEqual(validate_contract({"level": "low"}, [f]), [])
        violations = validate_contract({"level": "medium"}, [f])
        self.assertTrue(any(v.startswith("enum_violation:") for v in violations))

    def test_unknown_extra_keys_policy(self):
        f = FieldContract("a", "str")
        self.assertEqual(validate_contract({"a": "x", "zz": 1}, [f]), [])
        violations = validate_contract(
            {"a": "x", "zz": 1}, [f], allow_extra_keys=False
        )
        self.assertTrue(any(v.startswith("unknown_key:") for v in violations))

    def test_nested_json_validated_recursively_bounded_depth(self):
        f = FieldContract("tree", "json")

        def _nested(depth):
            node: dict = {"l": [1, 2]}
            for _ in range(depth):
                node = {"l": node}
            return {"tree": node}

        self.assertEqual(validate_contract(_nested(7), [f]), [])  # níveis ok
        too_deep = _nested(14)  # excede max_json_depth=10
        violations = validate_contract(too_deep, [f], max_json_depth=10)
        self.assertTrue(any("max depth" in v for v in violations))
        bad_key = {"tree": {1: "x"}}  # chave de dict não-str
        violations = validate_contract(bad_key, [f])
        self.assertTrue(any(v.startswith("json_invalid:") for v in violations))

    def test_not_a_dict_root(self):
        violations = validate_contract(["not", "a", "dict"], [])
        self.assertEqual(violations, ["not_a_dict: contract root is not a dict"])


class TestContractGuard(unittest.TestCase):
    def test_hard_raises_and_soft_is_recorded(self):
        # HARD: output exigido ausente do evidence -> raise.
        hard_contract = TaskIOContract(
            outputs=[FieldContract("executor", "str", required=True)]
        )
        guard = ContractGuard(hard_contract)
        result = {"status": "COMPLETED", "summary": "s",
                  "evidence": {"lane": "hermes"}, "residual_risk": []}
        with self.assertRaises(ContractViolationError) as ctx:
            guard.gate_outputs(result)
        self.assertTrue(ctx.exception.violations)

        # SOFT: apenas unknown_key (allow_extra_keys=False) -> anota e retorna.
        soft_contract = TaskIOContract(
            outputs=[FieldContract("lane", "str")], allow_extra_keys=False
        )
        soft_guard = ContractGuard(soft_contract)
        result2 = {"status": "COMPLETED", "summary": "s",
                   "evidence": {"lane": "hermes", "zz": 1}, "residual_risk": []}
        annotated = soft_guard.gate_outputs(result2)
        self.assertTrue(any(
            r.startswith("contract:unknown_key:") for r in annotated["residual_risk"]
        ))
        self.assertTrue(annotated["evidence"]["contract_violations"])
        # in_place=False não muta o original.
        result3 = {"status": "COMPLETED", "summary": "s",
                   "evidence": {"lane": "hermes", "zz": 1}, "residual_risk": []}
        soft_guard.gate_outputs(result3, in_place=False)
        self.assertEqual(result3["residual_risk"], [])

    def test_empty_contract_accepts_anything(self):
        guard = ContractGuard(empty_contract())
        payload = {"qualquer": {"coisa": [1, 2, {"aninhada": True}]}}
        self.assertEqual(guard.check_inputs(payload), [])
        self.assertEqual(guard.check_outputs(payload), [])
        guard.gate_inputs(payload)  # não levanta
        out = guard.gate_outputs({"evidence": {}, "summary": "",
                                  "residual_risk": []})
        self.assertEqual(out["residual_risk"], [])

    def test_contract_guard_default_hard_kinds(self):
        guard = ContractGuard(empty_contract())
        hard = guard.hard_kinds
        self.assertNotIn(ContractViolationKind.unknown_key.value, hard)
        for kind in (
            ContractViolationKind.not_a_dict, ContractViolationKind.missing_required,
            ContractViolationKind.type_mismatch, ContractViolationKind.enum_violation,
            ContractViolationKind.json_invalid,
        ):
            self.assertIn(kind.value, hard)


class TestTaskSpecIntegration(unittest.TestCase):
    def test_task_spec_additive_default_preserves_existing_usage(self):
        t = TaskSpec(id="T-1", title="Título", goal="Meta",
                     acceptance_criteria=[])
        self.assertIsNone(t.task_contract)
        self.assertEqual(t.id, "T-1")
        self.assertEqual(t.required_capabilities, [])

    def test_task_spec_carries_typed_contract(self):
        contract = TaskIOContract(
            inputs=[FieldContract("language", "str", required=True)],
            outputs=[FieldContract("lane", "str")],
        )
        t = TaskSpec(id="T-2", title="T", goal="G", task_contract=contract)
        self.assertEqual(t.task_contract.inputs[0].name, "language")
        # Round-trip do contrato em JSON (shape usado pelo KanbanAdapter).
        payload = json.loads(json.dumps({
            "inputs": [f.__dict__ for f in contract.inputs],
            "outputs": [f.__dict__ for f in contract.outputs],
            "allow_extra_keys": contract.allow_extra_keys,
        }))
        self.assertEqual(payload["inputs"][0]["name"], "language")

    def test_run_result_dict_passes_contract_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            spec = {"id": "T-C1", "required_capabilities": []}
            result = DeterministicLaneWorker().execute("T-C1", ws, spec)
            # Dict LaneWorker com namespace de saída = evidence.
            self.assertTrue({"status", "lane", "summary", "artifacts",
                             "evidence", "residual_risk"} <= set(result))
            ok_contract = TaskIOContract(outputs=[
                FieldContract("lane", "str", required=True),
                FieldContract("executor", "str", required=True),
                FieldContract("workspace", "path", required=True),
            ])
            guard = ContractGuard(ok_contract)
            self.assertEqual(guard.check_outputs(result), [])
            self.assertEqual(guard.gate_outputs(result)["residual_risk"], [])
            # Contrato errado -> HARD raise.
            bad_contract = TaskIOContract(outputs=[
                FieldContract("workspace", "int", required=True),
            ])
            with self.assertRaises(ContractViolationError):
                ContractGuard(bad_contract).gate_outputs(result)


if __name__ == "__main__":
    unittest.main()
