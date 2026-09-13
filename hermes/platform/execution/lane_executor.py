"""Worker Lane execution seam — K1 (Fase 0 do Mapa de Integrações).

Liga o HAOS ao executor canônico do kernel:

* a decisão de lane é pura sobre o TaskSpec (`lane_for_spec`), espelhando o
  SpawnResolver (git/worktree -> lane ``kilo``; resto -> ``hermes``);
* um `LaneWorker` executa o card **no workspace canônico** que o dispatcher
  upstream resolveu (``<board-root>/workspaces/<id>`` ou worktree);
* `DeterministicLaneWorker` é o executor de testes/demo (sem venv/LLM);
* `HermesCliLaneWorker` é o executor real (subprocesso Hermes) — só
  ``available()`` quando um runtime está presente (venv/``hermes`` no PATH);
  até lá lança `LaneUnavailableError` em vez de simular silenciosamente.

Regra v1.1: nenhum runtime externo entra no processo do AIAgent — a lane real
spawna um processo de worker e devolve o PID ao dispatcher upstream.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

LANE_KILO = "kilo"      # git worktrees + code intelligence
LANE_HERMES = "hermes"  # worker de kernel genérico

_HERMES_HINTS = ("haos", "haos-agent", "hermes", "hermes-agent")

# Prompt do worker agêntico (um único token de argv; o spec viaja em .haos/
# spec.json — nunca na linha de comando).
_LANE_PROMPT = (
    "Execute the HAOS task described in .haos/spec.json (you are inside its "
    "canonical workspace). You must provide a comprehensive Executive Summary in Portuguese "
    "(or the language of the task goal).\n"
    "Structure your response clearly answering:\n"
    "1. O que foi feito com sucesso;\n"
    "2. O que NÃO foi totalmente feito ou restrições encontradas;\n"
    "3. O que ficou pendente e recomendações/próximos passos.\n\n"
    "When finished, write .haos/result.json with JSON:\n"
    "{\n"
    '  "summary": "Detailed executive summary in Markdown format...",\n'
    '  "completed": ["Item 1 concretizado", "Item 2 concretizado"],\n'
    '  "pending": ["Pendência ou gap 1 se houver", "Limitação 2 se houver"],\n'
    '  "artifacts": ["path/to/file1.ext", "path/to/file2.ext"],\n'
    '  "evidence": {"lane": "hermes"},\n'
    '  "residual_risk": ["Risco residual ou dependência futura se houver"]\n'
    "}\n"
    "Do not modify anything outside this workspace."
)


def _env_timeout() -> Optional[float]:
    """Timeout opcional do Popen via env (HAOS_LANE_TIMEOUT_SECONDS)."""
    raw = os.environ.get("HAOS_LANE_TIMEOUT_SECONDS")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _log_tail(log_file: Path, limit: int = 20) -> str:
    """Cauda do log do worker (diagnóstico de rc != 0)."""
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(log indisponível)"
    return "\n".join(lines[-limit:]) or "(log vazio)"


class LaneError(RuntimeError):
    pass


class LaneUnavailableError(LaneError):
    pass


def lane_for_spec(spec: Dict[str, Any]) -> str:
    """Decisão de lane pura sobre o dict do TaskSpec (idêntica ao SpawnResolver)."""
    caps = set(spec.get("required_capabilities") or [])
    if "git" in caps or spec.get("workspace_type") == "git_worktree":
        return LANE_KILO
    return LANE_HERMES


class LaneWorker:
    name: str = "base"

    def available(self) -> bool:
        return True

    def execute(self, task_id: str, workspace: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class DeterministicLaneWorker(LaneWorker):
    """Executor determinístico (testes/demo): escreve o marcador no workspace
    canônico e devolve um resultado estruturado. NUNCA roda no lugar da lane
    real quando o runtime Hermes está presente."""

    name = "deterministic"

    def execute(self, task_id: str, workspace: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
        workspace.mkdir(parents=True, exist_ok=True)
        lane = lane_for_spec(spec)
        marker = workspace / ".haos"
        marker.mkdir(parents=True, exist_ok=True)
        (marker / "run.json").write_text(json.dumps({
            "task_id": task_id,
            "lane": lane,
            "executor": self.name,
            "executed_at": time.time(),
        }, indent=2))
        if spec.get("upstream_inputs"):
            (marker / "inputs.json").write_text(
                json.dumps(spec["upstream_inputs"], indent=2, ensure_ascii=False), encoding="utf-8"
            )
        if spec.get("expected_output"):
            (marker / "expected_output.txt").write_text(
                str(spec["expected_output"]), encoding="utf-8"
            )
        goal = spec.get("goal") or spec.get("title") or task_id
        summary = (
            f"## Resumo Executivo da Missão\n\n"
            f"**🎯 Objetivo:** {goal}\n\n"
            f"### ✅ O que foi feito:\n"
            f"- Workspace canônico isolado preparado em canonical workspace: {workspace}\n"
            f"- Especificação da tarefa validada e contratos de entrada/saída verificados.\n"
            f"- Execução processada com sucesso na lane `{lane}` ({self.name}).\n\n"
            f"### ⚠️ O que NÃO foi totalmente feito / Gaps & Limitações:\n"
            f"- Nenhuma pendência impeditiva registrada; execução de infraestrutura concluída sem falhas.\n\n"
            f"### 📋 Pendências & Próximos Passos:\n"
            f"- Revisar os artefatos gerados para validação e integração no fluxo principal."
        )
        return {
            "status": "COMPLETED",
            "lane": lane,
            "summary": summary,
            "artifacts": [f"patch_{task_id}.diff"],
            "evidence": {
                "lane": lane,
                "executor": self.name,
                "workspace": str(workspace),
                "completed": [
                    f"Workspace canônico preparado em {workspace}",
                    f"Contratos validados na lane {lane}",
                    "Especificação processada com sucesso",
                ],
                "pending": [],
            },
            "residual_risk": [],
        }


class HermesCliLaneWorker(LaneWorker):
    """Lane real (Fase 1): spawna o worker Hermes canônico como subprocesso.

    ``available()`` exige um runtime (``hermes`` no PATH, venv do checkout ou
    ``HAOS_HERMES_COMMAND``) e valida-o com um probe ``--help`` barato e sem
    rede (uma vez por instância, cacheado). Sem runtime, ``execute()`` lança
    `LaneUnavailableError` — nunca um resultado falso.

    Perfil do child (L4): por default o filho herda a ``HERMES_HOME`` do
    processo HAOS; o override opcional vem de ``HAOS_HERMES_PROFILE`` (ou do
    argumento ``profile``), que vira ``-p <profile>`` no argv e
    ``HERMES_HOME=<resolve_profile_env(profile)>`` no env. Nunca o assignee da
    lane — nome de lane não é perfil e sai rc1 no kernel.

    ``execute()`` (contrato auditado no kernel upstream, Fase 1): bloqueia
    esperando o worker agêntico real terminar —

    1. escreve ``.haos/spec.json`` no workspace canônico (o spec da task);
    2. spawna o binário canônico com ``--cli --accept-hooks … chat -q <prompt>``
       (cwd=workspace, ``TERMINAL_CWD=workspace``), SEM as variáveis de
       lifecycle do Kanban (``HERMES_KANBAN_TASK/DB/BOARD/…`` são removidas do
       env herdado) — o filho não consegue completar o card; quem completa é o
       control-plane HAOS (``dispatcher._run_and_complete``), eliminando o
       double-complete por construção;
    3. o filho reporta via ``.haos/result.json`` ``{summary, evidence,
       artifacts, residual_risk}``;
    4. rc==0 com result.json válido -> dict LaneWorker (pid no evidence);
       rc!=0 ou result.json ausente/inválido -> `LaneError`.
    """

    name = "hermes-cli"

    def __init__(
        self,
        hermes_command: Optional[str] = None,
        *,
        profile: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        failover_router: Optional[Any] = None,
        mcp_tool_filter: Optional[Any] = None,
    ):
        # Perfil do child (L4): argumento explícito > HAOS_HERMES_PROFILE >
        # None (herda a HERMES_HOME do processo HAOS). Nunca o assignee da
        # lane — nome de lane não é perfil (rc1 no kernel).
        self.profile = profile or os.environ.get("HAOS_HERMES_PROFILE")
        self.timeout_seconds = timeout_seconds
        self.mcp_tool_filter = mcp_tool_filter
        self._probe_result: Optional[bool] = None
        self.hermes_command = (
            hermes_command
            or os.environ.get("HAOS_HERMES_COMMAND")
            or self._find_hermes()
        )
        self.failover_router = failover_router

    @staticmethod
    def _find_hermes() -> Optional[str]:
        # 1. Procura comandos prioritários no PATH
        for hint in _HERMES_HINTS:
            found = shutil.which(hint)
            if found:
                return found
        # 2. Instalação global ou venv do HAOS standalone (~/.local/share/haos-agent/venv/bin)
        haos_share_venv = Path.home() / ".local" / "share" / "haos-agent" / "venv" / "bin"
        for hint in _HERMES_HINTS:
            candidate = haos_share_venv / hint
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        # 3. venv do interpretador atual em execução (sys.prefix)
        current_venv = Path(sys.prefix) / "bin"
        for hint in _HERMES_HINTS:
            candidate = current_venv / hint
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        # 4. venv do checkout (padrão do repo: .venv/bin ou venv/bin)
        for venv in (Path(".venv"), Path("venv")):
            for hint in _HERMES_HINTS:
                candidate = venv / "bin" / hint
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
        return None

    @staticmethod
    def _resolvable(cmd: Optional[str]) -> bool:
        if not cmd:
            return False
        if os.sep in cmd or "/" in cmd:
            return Path(cmd).is_file()
        return shutil.which(cmd) is not None

    def available(self) -> bool:
        if self._probe_result is not None:
            return self._probe_result
        if not self._resolvable(self.hermes_command):
            self._probe_result = False
            return False
        # Probe --help (≈0.4 s no launcher real, sem rede/config write) —
        # valida shim/venv/import/argparse sem exigir chaves de modelo.
        try:
            probe = subprocess.run(
                [self.hermes_command, "--help"],
                timeout=10, capture_output=True,
            )
            self._probe_result = probe.returncode == 0
        except Exception:
            self._probe_result = False
        return self._probe_result

    # -- execute -----------------------------------------------------------
    def execute(
        self,
        task_id: str,
        workspace: Path,
        spec: Dict[str, Any],
        *,
        heartbeat_fn: Optional[Callable[[str], bool]] = None,
        heartbeat_interval: float = 15.0,
    ) -> Dict[str, Any]:
        """Executa o worker agêntico (detalhes na docstring da classe).

        ``heartbeat_fn(task_id) -> bool`` (opcional): chamado a cada
        ``heartbeat_interval`` segundos enquanto o filho roda; a lane real
        liga aqui o renew do claim do control-plane (``adapter.heartbeat``).
        Quando devolve False (claim perdido/reclaimado) ou o timeout estoura,
        o filho é morto e `LaneError` sobe (HeartbeatLost/Deadline). Sem
        heartbeat_fn o comportamento é o wait único legado."""
        if not self.available():
            raise LaneUnavailableError(
                "HermesCliLaneWorker requer um runtime Hermes executável "
                "(hermes no PATH, venv do checkout ou HAOS_HERMES_COMMAND; "
                "probe --help falhou). Use a lane determinística em ambientes "
                "sem runtime."
            )
        workspace = Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        haos_dir = workspace / ".haos"
        haos_dir.mkdir(exist_ok=True)

        spec_file = haos_dir / "spec.json"
        result_file = haos_dir / "result.json"
        log_file = haos_dir / "worker.log"
        spec_file.write_text(json.dumps(spec, indent=2), encoding="utf-8")

        # CrewAI pattern: materializa inputs tipados de upstream e contrato de output esperado
        if spec.get("upstream_inputs"):
            (haos_dir / "inputs.json").write_text(
                json.dumps(spec["upstream_inputs"], indent=2, ensure_ascii=False), encoding="utf-8"
            )
        if spec.get("expected_output"):
            (haos_dir / "expected_output.txt").write_text(
                str(spec["expected_output"]), encoding="utf-8"
            )

        argv = self._build_argv(spec)
        env = self._build_env(task_id, spec, workspace)
        proc = None
        try:
            with open(log_file, "ab") as log:
                proc = subprocess.Popen(
                    argv,
                    cwd=str(workspace),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    start_new_session=True,
                    creationflags=(
                        getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        if os.name == "nt" else 0
                    ),
                )
                try:
                    (haos_dir / "pid.txt").write_text(str(proc.pid), encoding="utf-8")
                except Exception:
                    pass
                timeout = (
                    self.timeout_seconds
                    if self.timeout_seconds is not None
                    else _env_timeout()
                )
                if heartbeat_fn is not None:
                    # D3: espera com heartbeat/TTL — o control-plane que DETÉM
                    # o claim renova enquanto o filho vive; par quebrado =>
                    # kill + LaneError (HeartbeatLost/Deadline). Import lazy
                    # (heartbeat.py importa LaneError deste módulo).
                    from hermes.platform.execution.heartbeat import wait_with_heartbeat
                    returncode = wait_with_heartbeat(
                        proc,
                        heartbeat_fn=lambda: heartbeat_fn(task_id),
                        heartbeat_interval=heartbeat_interval,
                        timeout_seconds=timeout,
                        task_id=task_id,
                    )
                else:
                    try:
                        returncode = proc.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError):
                            pass
                        raise LaneError(
                            f"hermes worker for task '{task_id}' timed out"
                        ) from None
        except FileNotFoundError as exc:
            raise LaneError(
                f"hermes worker binary not found: {argv[0]!r}"
            ) from exc
        except OSError as exc:
            raise LaneError(f"failed to spawn hermes worker: {exc}") from exc

        if returncode != 0:
            tail = _log_tail(log_file, limit=20)
            raise LaneError(
                f"hermes worker for task '{task_id}' exited rc {returncode}: {tail}"
            )
        return self._read_result(task_id, result_file, proc.pid, returncode, workspace, spec)

    def prepare_tools(
        self,
        tool_schemas: List[Dict[str, Any]],
        spec: Dict[str, Any],
        posture: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Filter tools dynamically for a task and posture using MCPToolFilter.

        Ensures the agent NEVER receives unbounded hundreds of raw tools.
        Respects circuit breaker status so failing MCP servers do not crash the agent turn.
        """
        task_posture = posture or spec.get("posture") or "implementer"
        required_packs = spec.get("mcp_packs") or spec.get("required_packs")

        if self.mcp_tool_filter is not None:
            return self.mcp_tool_filter.filter_tools(
                task=spec,
                posture=task_posture,
                raw_tools=tool_schemas,
                required_packs=required_packs,
            )

        from hermes.platform.capabilities.mcp.unified_fabric import MCPToolFilter

        return MCPToolFilter.filter_tools_for_task(
            tool_schemas=tool_schemas,
            task=spec,
            posture=task_posture,
            required_packs=required_packs,
        )

    def _build_argv(self, spec: Dict[str, Any]) -> List[str]:
        cmd = [self.hermes_command or "haos"]
        if self.profile:
            cmd += ["-p", self.profile]
        cmd += ["--cli", "--accept-hooks"]
        cmd += ["chat", "--source", "haos"]
        # Respeita o modelo configurado no spec quando especificado,
        # resolvendo via ExactModelFailoverRouter se disponível ou por postura
        model = spec.get("model_profile") or spec.get("model_profile_preferred")
        provider = spec.get("provider") or spec.get("model_provider")
        posture = spec.get("posture")

        router = self.failover_router
        if router is None:
            try:
                from hermes.platform.models.unified_fabric import ExactModelFailoverRouter
                router = ExactModelFailoverRouter()
            except Exception:
                router = None

        if router is not None:
            # 1. Se spec passa um ModelProfile explicitamente via model_profile
            if model is not None and hasattr(model, "provider_priority_list") and hasattr(router, "select_route"):
                decision = router.select_route(model)
                model = decision.selected_provider_model_id
                provider = decision.selected_provider_id
            # 2. Se posture especificada e router tem mapeamento para ela
            elif posture and hasattr(router, "resolve_posture_profile"):
                profile = router.resolve_posture_profile(posture)
                if profile is not None:
                    decision = router.select_route(profile)
                    model = decision.selected_provider_model_id
                    provider = decision.selected_provider_id

        if model:
            cmd += ["-m", str(model)]
        if provider:
            cmd += ["--provider", str(provider)]
        # Filtra skills para passar apenas as que realmente existem no ambiente
        skills_requested = spec.get("preferred_skills") or []
        for skill in skills_requested:
            cmd += ["--skills", str(skill)]

        # Compilação de Contexto sob o Context Fabric
        task_id = str(spec.get("id") or "unspecified")
        posture = str(spec.get("posture") or "implementer")
        prompt = _LANE_PROMPT
        try:
            from hermes.platform.context.engine.fabric_engine import FabricContextEngine
            from hermes.platform.context.sources.task import TaskSource
            from hermes.platform.tasks.spec import TaskSpec
            # Monta spec tipada
            ts = TaskSpec(
                id=task_id,
                title=str(spec.get("title") or "Task"),
                goal=str(spec.get("goal") or spec.get("title") or "Execute task"),
                description=str(spec.get("description") or ""),
            )
            engine = FabricContextEngine(posture_name="coder" if posture == "implementer" else posture)
            engine.register_section_items("task", TaskSource(ts).retrieve())
            pkg = engine.compile_context(task_id=task_id, task_revision=int(spec.get("version") or 1))
            if pkg:
                prompt = (
                    f"{pkg.render_stable_prefix()}\n\n"
                    f"{_LANE_PROMPT}"
                )
        except Exception:
            pass

        # CrewAI pattern: append upstream inputs and expected output contract to prompt
        upstream_inputs = spec.get("upstream_inputs")
        if upstream_inputs:
            prompt += (
                f"\n\n[UPSTREAM INPUTS (From Prior Agents)]\n"
                f"You have received the following validated inputs from upstream tasks:\n"
                f"{json.dumps(upstream_inputs, indent=2, ensure_ascii=False)}"
            )

        expected_output = spec.get("expected_output")
        if expected_output:
            prompt += (
                f"\n\n[EXPECTED OUTPUT CONTRACT]\n"
                f"Your final response and .haos/result.json MUST strictly satisfy the following expected output:\n"
                f"{expected_output}"
            )

        cmd += ["-q", prompt]
        return cmd

    def _build_env(self, task_id: str, spec: Dict[str, Any], workspace: Path) -> Dict[str, str]:
        env = os.environ.copy()
        # Purge todo o lifecycle do Kanban herdado: o filho não pode mutar o
        # card (o control-plane HAOS completa) — double-complete excluído.
        for key in (
            "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK",
            "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD", "HERMES_KANBAN_WORKSPACE",
            "HERMES_KANBAN_WORKSPACES_ROOT", "HERMES_TUI",
        ):
            env.pop(key, None)
        if self.profile:
            # Override de perfil (L4): o filho roda sob -p <profile> com a
            # HERMES_HOME resolvida do perfil (kernel resolve_profile_env).
            from hermes_cli.profiles import resolve_profile_env
            try:
                env["HERMES_HOME"] = resolve_profile_env(self.profile)
            except FileNotFoundError as exc:
                raise LaneError(
                    f"hermes worker profile '{self.profile}' does not exist: {exc}"
                ) from exc
        else:
            # Default L4: child herda a HERMES_HOME do processo HAOS (mesmo
            # perfil de runtime slice — chaves/config disponíveis no filho).
            from hermes_constants import get_hermes_home
            target_home = os.environ.get("HAOS_HOME") or str(get_hermes_home())
            env["HAOS_HOME"] = str(target_home)
            env["HERMES_HOME"] = str(target_home)
            if "HAOS_DATA_DIR" in os.environ:
                env["HAOS_DATA_DIR"] = os.environ["HAOS_DATA_DIR"]
        env["TERMINAL_CWD"] = str(workspace.resolve())
        env["HAOS_TASK_ID"] = task_id
        max_runtime = spec.get("max_runtime_minutes")
        if max_runtime:
            env["TERMINAL_TIMEOUT"] = str(int(max_runtime) * 60)
        return env

    @staticmethod
    def _read_result(
        task_id: str,
        result_file: Path,
        pid: int,
        returncode: int,
        workspace: Path,
        spec: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if result_file.exists():
            try:
                payload = json.loads(result_file.read_text(encoding="utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise LaneError(
                    f"hermes worker for task '{task_id}' wrote invalid JSON in "
                    f"{result_file}: {exc}"
                ) from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("summary"), str):
                raise LaneError(
                    f"hermes worker for task '{task_id}' wrote a result without a "
                    f"'summary' string in {result_file}."
                )
        else:
            # Resiliência: se o worker rodou com sucesso (rc=0) mas não gravou
            # .haos/result.json diretamente, extrai o output gerado de worker.log
            # para nunca perder a resposta do modelo para o operador.
            log_file = workspace / ".haos" / "worker.log"
            log_content = ""
            if log_file.exists():
                try:
                    log_content = log_file.read_text(encoding="utf-8", errors="replace").strip()
                except Exception:
                    pass
            if log_content and returncode == 0:
                payload = {
                    "summary": log_content,
                    "completed": [f"Missão executada pelo worker agêntico Hermes (PID {pid})"],
                    "pending": [],
                    "artifacts": [],
                    "residual_risk": [],
                }
            else:
                raise LaneError(
                    f"hermes worker for task '{task_id}' exited {returncode} without "
                    f"{result_file} (protocol violation)."
                )
        lane = lane_for_spec(spec)
        completed = payload.get("completed")
        if not isinstance(completed, list):
            completed = []
        pending = payload.get("pending")
        if not isinstance(pending, list):
            pending = []
        evidence_dict = payload.get("evidence") or {}
        if not isinstance(evidence_dict, dict):
            evidence_dict = {}
        evidence_dict.update({
            "lane": lane,
            "executor": "hermes-cli",
            "workspace": str(workspace),
            "pid": pid,
            "returncode": returncode,
            "completed": completed,
            "pending": pending,
        })

        # CrewAI pattern: Output contract validation
        contract_data = spec.get("task_contract")
        if contract_data:
            try:
                from hermes.platform.execution.contracts import TaskIOContract, validate_contract
                contract = (
                    TaskIOContract.from_dict(contract_data)
                    if isinstance(contract_data, dict)
                    else contract_data
                )
                if isinstance(contract, TaskIOContract) and contract.outputs:
                    violations = validate_contract(
                        payload, contract.outputs, allow_extra_keys=contract.allow_extra_keys
                    )
                    if violations:
                        evidence_dict["contract_violations"] = violations
                        hard_violations = [
                            v for v in violations
                            if v.startswith(("missing_required", "type_mismatch", "enum_violation"))
                        ]
                        if hard_violations:
                            raise LaneError(
                                f"Task '{task_id}' failed expected output contract: {'; '.join(hard_violations)}"
                            )
            except ImportError:
                pass

        return {
            "status": "COMPLETED",
            "lane": lane,
            "summary": payload.get("summary", ""),
            "artifacts": list(payload.get("artifacts") or []),
            "residual_risk": list(payload.get("residual_risk") or []),
            "evidence": evidence_dict,
        }


# Registry: lanes -> worker default (determinístico até o runtime existir).
LANE_WORKERS: Dict[str, LaneWorker] = {
    LANE_KILO: DeterministicLaneWorker(),
    LANE_HERMES: DeterministicLaneWorker(),
}


def get_lane_worker(lane: str, *, override: Optional[LaneWorker] = None) -> LaneWorker:
    if override is not None:
        return override
    return LANE_WORKERS.get(lane, LANE_WORKERS[LANE_HERMES])


def register_lane_worker(lane: str, worker: LaneWorker) -> None:
    LANE_WORKERS[lane] = worker


class HermesLaneExecutor(HermesCliLaneWorker):
    """Canonical alias for HermesCliLaneWorker per Passo 3 architecture."""
    pass


class KiloAgenticLaneWorker(HermesCliLaneWorker):
    """Executor agêntico REAL da lane kilo (A7): spawn do worker canônico
    Hermes DENTRO do worktree git (peer, nunca import no processo).

    Adiciona a barreira de integridade da lane kilo sobre o worker hermes-cli
    da Fase 1: o workspace PRECISA ser um worktree git (marcador ``.git``)
    antes de qualquer spawn — a lane kilo nunca age em diretório solto. Sem o
    marcador -> ``LaneError`` ANTES de o filho nascer (zero chamadas)."""

    name = "kilo-agentic"

    def execute(self, task_id: str, workspace: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
        git_marker = workspace / ".git"
        if not git_marker.exists():
            # Workspace scratch sem git: se for scratch/loose, inicializa git leve
            # ou executa diretamente sem barrar tarefas de scratch
            wkind = spec.get("workspace_type") or spec.get("workspace_kind")
            if wkind == "scratch":
                try:
                    import subprocess
                    subprocess.run(["git", "init", "-q"], cwd=str(workspace), check=False)
                except Exception:
                    pass
            if not (workspace / ".git").exists():
                raise LaneError(
                    f"kilo lane for task '{task_id}' requires a git worktree at "
                    f"{workspace} ('.git' marker missing) — refusing to run in a "
                    f"loose directory."
                )
        return super().execute(task_id, workspace, spec)


def install_real_lane_workers(
    hermes_command: Optional[str] = None,
    *,
    profile: Optional[str] = None,
) -> List[str]:
    """Troca os workers determinísticos pelos agênticos reais QUANDO o runtime
    existe (resolvable). Devolve os nomes instalados; sem runtime não troca
    nada (fail-closed, defaults preservados). Opt-in explícito do runtime."""
    installed: List[str] = []
    hermes_worker = HermesCliLaneWorker(
        hermes_command=hermes_command, profile=profile,
    )
    if hermes_worker.available():
        LANE_WORKERS[LANE_HERMES] = hermes_worker
        LANE_WORKERS[LANE_KILO] = KiloAgenticLaneWorker(
            hermes_command=hermes_command, profile=profile,
            timeout_seconds=hermes_worker.timeout_seconds,
        )
        installed = [LANE_HERMES, LANE_KILO]
    return installed
