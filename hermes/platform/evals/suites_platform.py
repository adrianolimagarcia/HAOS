"""A6 — suites de evals REAIS do platform (offline, determinísticas).

Não comparam LLMs (sem runtime externo no processo): medem o FORK de verdade
sobre a árvore atual — compile, PEP-420, lint stdlib-only em nível de módulo,
seams importáveis, invariante de rota exata do modelo e métricas de baseline.
Cada op é um caso EvalCase(input={"op": ..., "path"|"module"|...}) processado
pelo ``run_fork_case``; resultados viram baseline via BaselineStore com o
rótulo do candidato ("haos-fork" hoje; "model-a"/"model-b" quando houver LLM).

Contratos (invariantes que o eval exige, não snapshots):
- compileall rc==0; sem __init__.py em hermes/platform (PEP-420); imports de
  topo restritos a stdlib ∪ {hermes.platform, hermes_cli}; seams importam;
  ExactModelRouter nunca troca de modelo (identity preservada por perfil).
"""

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from hermes.platform.evals.baselines import BaselineStore
from hermes.platform.evals.runner import EvalCase, EvalSuite

PLATFORM_REL = Path("hermes/platform")
# Imports de topo permitidos além da stdlib (seams canônicos e o próprio HAOS).
_ALLOWED_TOP_IMPORTS = {"hermes", "hermes_cli", "agent"}
# Exceção DOCUMENTADA (delta 45, INTEGRATIONS §7/§8.3): `ui/dashboard_plugin/`
# é um host adapter — só o processo do Web Dashboard oficial o importa (e o
# dashboard JÁ depende de fastapi); nunca é importado pelo AIAgent nem por
# outro módulo do platform. Por isso seu import de fastapi fica fora do lint
# de "stdlib-only nível de módulo".
_STDLIB_LINT_EXCLUDE_DIRS = {"ui/dashboard_plugin"}


def platform_root(repo_root: Path) -> Path:
    return repo_root / PLATFORM_REL


def resolve_repo_root() -> Path:
    """Raiz do repositório (evals ficam em <root>/hermes/platform/evals)."""
    return Path(__file__).resolve().parents[3]


def _run(argv: List[str], root: Path, env_extra: Dict[str, str] | None = None) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run([sys.executable] + argv, cwd=str(root), env=env,
                          capture_output=True, text=True, timeout=180)
    return proc.returncode


# ---------------------------------------------------------------------- #
# Ops reais (cada uma é um EvalCase)
# ---------------------------------------------------------------------- #
def op_compileall(root: Path) -> Dict[str, Any]:
    rc = _run(["-m", "compileall", "-q", str(platform_root(root))], root)
    return {"passed": rc == 0, "score": 1.0 if rc == 0 else 0.0,
            "meta": {"compileall_rc": rc}}


def op_pep420(root: Path) -> Dict[str, Any]:
    offenders = [str(p.relative_to(root))
                 for p in platform_root(root).rglob("__init__.py")]
    return {"passed": not offenders, "score": 1.0 if not offenders else 0.0,
            "meta": {"init_files": offenders}}


def op_stdlib_lint(root: Path) -> Dict[str, Any]:
    """Imports de topo (corpo do módulo) restritos a stdlib + seams permitidos."""
    offenders: List[str] = []
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    for py in platform_root(root).rglob("*.py"):
        rel_dir = py.relative_to(platform_root(root)).parent.as_posix()
        if rel_dir in _STDLIB_LINT_EXCLUDE_DIRS:
            continue  # host adapter do dashboard (ver docstring da constante)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        for node in tree.body:  # só nível de módulo (funções ficam de fora)
            names: List[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                root_name = name.split(".")[0]
                if root_name and root_name not in stdlib \
                        and root_name not in _ALLOWED_TOP_IMPORTS:
                    offenders.append(f"{py.relative_to(root)}: {name}")
    return {"passed": not offenders, "score": 1.0 if not offenders else 0.0,
            "meta": {"module_level_imports_outside_stdlib": offenders}}


def op_seams_import(root: Path, modules: List[str]) -> Dict[str, Any]:
    failed = []
    for module in modules:
        rc = _run(["-c", f"import {module}"], root)
        if rc != 0:
            failed.append(module)
    return {"passed": not failed, "score": 1.0 if not failed else 0.0,
            "meta": {"seams": modules, "failed": failed}}


def op_model_exact_route(root: Path) -> Dict[str, Any]:
    """Identity exata do modelo preservada na rota escolhida (por perfil real)."""
    import hermes.platform.models.model_resolver as mr
    from hermes.platform.models.circuit_breaker import CircuitBreaker
    from hermes.platform.models.provider_router import ExactModelRouter
    profiles_file = platform_root(root) / "configs" / "defaults" / "model_profiles.json"
    declared_raw = json.loads(profiles_file.read_text(encoding="utf-8"))
    inner = declared_raw.get("model_profiles")
    declared = inner if isinstance(inner, dict) else declared_raw
    router = ExactModelRouter(circuit_breaker=CircuitBreaker())
    resolver = mr.ModelResolver()
    issues = []
    for profile_id in list(declared):
        if profile_id.startswith("_"):  # chaves de comentário no JSON
            continue
        try:
            profile = resolver.resolve(profile_id)
        except Exception as exc:
            issues.append(f"{profile_id}: {exc}")
            continue
        try:
            route = router.select_route(profile)
        except Exception as exc:
            issues.append(f"{profile_id} route: {exc}")
            continue
        # A rota escolhida SEMPRE pertence à cadeia do próprio perfil — o
        # router nunca inventa rota nem troca de modelo (nomes de provider
        # variam por provider; o vínculo é a rota do perfil exato).
        in_chain = any(
            r.provider_id == route.provider_id
            and r.provider_model_id == route.provider_model_id
            for r in profile.routes
        )
        if not in_chain:
            issues.append(
                f"{profile_id}: selected route "
                f"({route.provider_id}/{route.provider_model_id}) not in profile chain"
            )
    return {"passed": not issues, "score": 1.0 if not issues else 0.0,
            "meta": {"profiles": len(declared), "issues": issues}}


def collect_fork_metrics(root: Path) -> Dict[str, Any]:
    """Métricas leves do fork para o BaselineStore (rápidas, sem rodar a suíte
    inteira de testes — o saldo da suíte é reportado pelo próprio CI)."""
    py_files = list(platform_root(root).rglob("*.py"))
    test_files = list((root / "tests" / "platform").rglob("test_*.py"))
    seam_probe = ["hermes.platform.execution.dispatcher",
                  "hermes.platform.capabilities.modality.workers_cli",
                  "hermes.platform.memory.context_engine.engine"]
    seams = op_seams_import(root, seam_probe)
    pep = op_pep420(root)
    lint = op_stdlib_lint(root)
    return {
        "module_py_files": len(py_files),
        "test_files": len(test_files),
        "pep420": pep["passed"],
        "stdlib_lint": lint["passed"],
        "seams_ok": seams["passed"],
        "seams_checked": len(seam_probe),
        "checked_at": time.time(),
    }


def save_fork_metrics(store: BaselineStore, root: Path, label: str = "haos-fork") -> int:
    return store.save("platform.fork.metrics", label, collect_fork_metrics(root))


def build_platform_suites(root: Path) -> List[EvalSuite]:
    """Suites reais prontas para o EvalRunner (rótulos p/ upstream vs fork)."""
    seam_modules = [
        "hermes.platform.execution.dispatcher",
        "hermes.platform.execution.classify",
        "hermes.platform.execution.preferences",
        "hermes.platform.capabilities.modality.workers_cli",
        "hermes.platform.memory.context_engine.engine",
        "hermes.platform.assembly",
    ]
    return [
        EvalSuite(
            id="platform.fork.structure",
            description="compile + PEP-420 + stdlib-only module-level",
            cases=[
                EvalCase(id="compileall", input={"op": "compileall"}),
                EvalCase(id="pep420", input={"op": "pep420"}),
                EvalCase(id="stdlib_lint", input={"op": "stdlib_lint"}),
            ],
        ),
        EvalSuite(
            id="platform.fork.seams",
            description="seams canônicos importáveis em python puro",
            cases=[
                EvalCase(id="seams", input={"op": "seams",
                                            "modules": seam_modules}),
            ],
        ),
        EvalSuite(
            id="platform.fork.model_exact_route",
            description="ExactModelRouter preserva a identity exata do modelo",
            cases=[
                EvalCase(id="exact_route", input={"op": "model_exact_route"}),
            ],
        ),
    ]


def run_fork_case(root: Path, case: EvalCase) -> Dict[str, Any]:
    op = (case.input or {}).get("op")
    if op == "compileall":
        return op_compileall(root)
    if op == "pep420":
        return op_pep420(root)
    if op == "stdlib_lint":
        return op_stdlib_lint(root)
    if op == "seams":
        return op_seams_import(root, (case.input or {}).get("modules") or [])
    if op == "model_exact_route":
        return op_model_exact_route(root)
    return {"passed": False, "score": 0.0,
            "meta": {"error": f"unknown op {op!r}"}}


def run_all_real(root: Path, label: str = "haos-fork") -> Dict[str, Any]:
    """Orquestra as suites reais + persiste baseline de métricas."""
    from hermes.platform.evals.runner import EvalRunner
    runner = EvalRunner()
    results = []
    for suite in build_platform_suites(root):
        result = runner.run_suite(
            suite, lambda case, r=root: run_fork_case(r, case), label=label)
        results.append(result.to_dict())
    return {"label": label, "suites": results}
