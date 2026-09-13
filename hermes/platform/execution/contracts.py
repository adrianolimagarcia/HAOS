"""Contratos tipados de I/O para a fronteira TaskSpec -> lane (Fase 1, Agno).

O padrão vem do Agno (``input_schema``/``output_schema`` em cada fronteira de
Agent/Team): o que ENTRA na execução e o que VOLTA dela são validados contra
esquemas explícitos. Aqui o contrato é stdlib/dataclass — sem pydantic — e de
nível de CAMPO, para payloads ``dict`` (Agno valida objetos pydantic; o HAOS
trabalha com dicts JSON round-trippáveis entre control-plane e worker).

Semântica de falha (espelhando Agno):
- HARD  (missing_required/type_mismatch/enum_violation/json_invalid/not_a_dict)
        -> ``ContractViolationError`` lançada: bloqueia a execução/lane, como o
        Agno flipa a run para ``RunStatus.error`` (bloqueado, sem retry).
- SOFT  (unknown_key quando ``allow_extra_keys=False``) -> registrado em
        ``residual_risk`` + ``evidence["contract_violations"]`` e segue.

Módulo folha: não importa nada do hermes.platform nem runtime externo.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

FIELD_TYPES: FrozenSet[str] = frozenset({
    "str", "int", "float", "bool", "json", "path", "artifact_ref",
})


class ContractViolationKind(str, Enum):
    """Tipos estáveis de violação (prefixo de cada mensagem)."""

    not_a_dict = "not_a_dict"
    missing_required = "missing_required"
    type_mismatch = "type_mismatch"
    enum_violation = "enum_violation"
    json_invalid = "json_invalid"
    unknown_key = "unknown_key"


@dataclass(frozen=True)
class FieldContract:
    """Um campo tipado do payload de entrada/saída.

    ``type`` ∈ FIELD_TYPES. ``required=True`` sem ``default`` exige presença;
    ``default`` presente satisfaz a presença quando ausente. ``enum`` restringe
    o valor (checado após o tipo). Semântica por tipo:
    - str/int/float/bool  — isinstance estrito (int NÃO aceita bool; float não
      aceita int).
    - path               — str não-vazia sem NUL (referência a caminho).
    - artifact_ref       — str não-vazia (ref git://, artifact:// ou id de
      PerceptionArtifact — referência, nunca um novo artefato).
    - json               — valor JSON-safety recursivo com profundidade máxima.
    """

    name: str
    type: str = "str"
    required: bool = False
    description: str = ""
    default: Any = None
    enum: Optional[Tuple[Any, ...]] = None


@dataclass
class TaskIOContract:
    """Contrato de I/O de uma task: campos esperados no payload e no resultado."""

    inputs: List[FieldContract] = field(default_factory=list)
    outputs: List[FieldContract] = field(default_factory=list)
    allow_extra_keys: bool = True  # política "permitido por padrão" (Agno loose)

    def is_empty(self) -> bool:
        return not self.inputs and not self.outputs

    def to_dict(self) -> Dict[str, Any]:
        """Shape JSON round-trippable (persistido no KanbanAdapter._spec_dict)."""
        return {
            "inputs": [_field_dict(f) for f in self.inputs],
            "outputs": [_field_dict(f) for f in self.outputs],
            "allow_extra_keys": self.allow_extra_keys,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskIOContract":
        return cls(
            inputs=[_field_from_dict(f) for f in data.get("inputs") or []],
            outputs=[_field_from_dict(f) for f in data.get("outputs") or []],
            allow_extra_keys=data.get("allow_extra_keys", True),
        )


def _field_dict(f: FieldContract) -> Dict[str, Any]:
    out = {
        "name": f.name, "type": f.type, "required": f.required,
        "description": f.description,
    }
    if f.default is not None:
        out["default"] = f.default
    if f.enum is not None:
        out["enum"] = list(f.enum)  # tuple -> list p/ JSON
    return out


def _field_from_dict(data: Dict[str, Any]) -> FieldContract:
    enum = data.get("enum")
    return FieldContract(
        name=data["name"],
        type=data.get("type", "str"),
        required=data.get("required", False),
        description=data.get("description", ""),
        default=data.get("default"),
        enum=tuple(enum) if enum is not None else None,
    )


def empty_contract() -> TaskIOContract:
    """Contrato vazio == aceita qualquer coisa (padrão de TaskSpec)."""
    return TaskIOContract()


# --------------------------------------------------------------------------
# Validação pura
# --------------------------------------------------------------------------

def validate_contract(
    data: Any,
    fields: List[FieldContract],
    *,
    allow_extra_keys: bool = True,
    max_json_depth: int = 10,
) -> List[str]:
    """Valida ``data`` contra ``fields``; retorna lista de violações (vazia = ok).

    Pura e determinística; cada violação é ``"{kind}: {detalhe}"``.
    """
    violations: List[str] = []
    if not isinstance(data, dict):
        return ["not_a_dict: contract root is not a dict"]
    if not allow_extra_keys:
        declared = {f.name for f in fields}
        for key in data:
            if key not in declared:
                violations.append(f"unknown_key: '{key}'")
    for f in fields:
        if f.name not in data:
            if f.required and f.default is None:
                violations.append(f"missing_required: '{f.name}'")
            continue  # ausente + opcional (ou com default) nunca viola
        value = data[f.name]
        _validate_value(f, value, violations, max_json_depth)
    return violations


def _validate_value(
    f: FieldContract,
    value: Any,
    violations: List[str],
    max_json_depth: int,
) -> None:
    name = f.name
    ok = True
    if f.type == "str":
        ok = isinstance(value, str)
    elif f.type == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif f.type == "float":
        ok = isinstance(value, float) and not isinstance(value, bool)
    elif f.type == "bool":
        ok = isinstance(value, bool)
    elif f.type == "path":
        ok = isinstance(value, str) and bool(value) and "\x00" not in value
    elif f.type == "artifact_ref":
        ok = isinstance(value, str) and bool(value)
    elif f.type == "json":
        if not _json_is_safe(value, max_json_depth):
            violations.append(
                f"json_invalid: '{name}' exceeds max depth {max_json_depth} "
                f"or contains non-JSON values"
            )
        return  # json não passa por type_mismatch/enum (o valor é livre)
    else:  # tipo de contrato desconhecido -> definição inválida, tratar como ok
        return
    if not ok:
        violations.append(f"type_mismatch: '{name}' expected {f.type}")
        return
    if f.enum is not None and value not in f.enum:
        violations.append(
            f"enum_violation: '{name}' value {value!r} not in {f.enum}"
        )


def _json_is_safe(value: Any, max_depth: int) -> bool:
    """JSON-safety recursivo com limite de profundidade (bool é escalar JSON)."""

    def _walk(node: Any, depth: int) -> bool:
        if depth > max_depth:
            return False
        if node is None or isinstance(node, (str, int, float, bool)):
            return True
        if isinstance(node, dict):
            if depth + 1 > max_depth:
                return False
            for k, v in node.items():
                if not isinstance(k, str) or not _walk(v, depth + 1):
                    return False
            return True
        if isinstance(node, (list, tuple)):
            if depth + 1 > max_depth:
                return False
            return all(_walk(item, depth + 1) for item in node)
        return False  # bytes/set/frozenset/objetos arbitrários

    return _walk(value, 0)


# --------------------------------------------------------------------------
# Erro + guarda
# --------------------------------------------------------------------------

class ContractViolationError(Exception):
    """Falha HARD de contrato: bloqueia a execução/lane."""

    def __init__(self, violations: List[str]):
        self.violations = list(violations)
        super().__init__("\n".join(self.violations))


@dataclass(frozen=True)
class ContractGuard:
    """Guarda de contrato: HARD falha (raise); SOFT registra e segue.

    HARD por padrão: not_a_dict/missing_required/type_mismatch/enum_violation/
    json_invalid. SOFT: unknown_key (chaves extras não permitidas são apenas
    anotadas em residual_risk + evidence["contract_violations"]).
    """

    contract: TaskIOContract
    max_json_depth: int = 10
    hard_kinds: FrozenSet[str] = frozenset(
        kind.value for kind in ContractViolationKind
        if kind != ContractViolationKind.unknown_key
    )

    # -- checks ------------------------------------------------------------
    def check_inputs(self, payload: Dict[str, Any]) -> List[str]:
        return validate_contract(
            payload, self.contract.inputs,
            allow_extra_keys=self.contract.allow_extra_keys,
            max_json_depth=self.max_json_depth,
        )

    def check_outputs(self, result: Dict[str, Any]) -> List[str]:
        # Namespace de saída = evidence do dict LaneWorker (o que o worker
        # declara ter entregue); sem "evidence" + outputs exigidos => as
        # violações de missing_required surgem naturalmente do dict vazio.
        data = result.get("evidence", {})
        return validate_contract(
            data, self.contract.outputs,
            allow_extra_keys=self.contract.allow_extra_keys,
            max_json_depth=self.max_json_depth,
        )

    # -- gates -------------------------------------------------------------
    def gate_inputs(self, payload: Dict[str, Any]) -> None:
        """HARD -> raise ContractViolationError; só SOFT -> retorna em silêncio."""
        hard = [v for v in self.check_inputs(payload)
                if v.split(":", 1)[0] in self.hard_kinds]
        if hard:
            raise ContractViolationError(hard)

    def gate_outputs(
        self, result: Dict[str, Any], *, in_place: bool = True
    ) -> Dict[str, Any]:
        """HARD -> raise (bloqueia complete_task); SOFT -> anota e devolve.

        Soft registrado em residual_risk ("contract:<violação>") e em
        evidence["contract_violations"] — os dois sinks já existem no
        TaskResult/runs + complete_task.
        """
        all_v = self.check_outputs(result)
        hard = [v for v in all_v if v.split(":", 1)[0] in self.hard_kinds]
        if hard:
            raise ContractViolationError(hard)
        soft = [v for v in all_v if v.split(":", 1)[0] not in self.hard_kinds]
        if not soft:
            return result
        target = result if in_place else dict(result)
        residual = list(target.get("residual_risk") or [])
        residual.extend(f"contract:{v}" for v in soft)
        target["residual_risk"] = residual
        evidence = dict(target.get("evidence") or {})
        evidence.setdefault("contract_violations", []).extend(soft)
        target["evidence"] = evidence
        return target
