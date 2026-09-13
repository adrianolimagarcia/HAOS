from typing import Dict, Any, Optional

class FailureCategory:
    PROVIDER_ERROR = "provider_error"          # 429, 500, gateway timeout, failover de provider (NÃO conta retry de Task)
    TOOL_TRANSIENT = "tool_transient"          # timeout temporário em tool, socket reset (reexecuta tool)
    WORKER_CRASH = "worker_crash"              # SIGKILL, OOM, exceção não tratada no processo (conta Run retry / circuit breaker)
    REVIEW_REJECTION = "review_rejection"      # reviewer rejeitou ou solicitou mudanças (Rework, NÃO é falha de infra)
    ACCEPTANCE_FAILURE = "acceptance_failure"  # critério de aceite mecânico falhou (tests, lsp, schema)
    PROTOCOL_VIOLATION = "protocol_violation"  # violação de contrato I/O ou schema de mensagens
    DEPENDENCY_MISSING = "dependency_missing"  # dependência upstream não resolvida
    CAPABILITY_MISSING = "capability_missing"  # capability exigida indisponível
    AUTH_ERROR = "auth_error"                  # token inválido ou sem permissão (bloqueia workflow para credenciais)
    BUDGET_EXCEEDED = "budget_exceeded"        # estouro de custo, tempo ou tokens
    HUMAN_INPUT_REQUIRED = "human_input_required" # bloqueio aguardando resposta humana

class FailureClassifier:
    CATEGORIES = {
        "PROVIDER_ERROR": FailureCategory.PROVIDER_ERROR,
        "TOOL_TRANSIENT": FailureCategory.TOOL_TRANSIENT,
        "WORKER_CRASH": FailureCategory.WORKER_CRASH,
        "REVIEW_REJECTION": FailureCategory.REVIEW_REJECTION,
        "ACCEPTANCE_FAILURE": FailureCategory.ACCEPTANCE_FAILURE,
        "PROTOCOL_VIOLATION": FailureCategory.PROTOCOL_VIOLATION,
        "DEPENDENCY_MISSING": FailureCategory.DEPENDENCY_MISSING,
        "CAPABILITY_MISSING": FailureCategory.CAPABILITY_MISSING,
        "AUTH_ERROR": FailureCategory.AUTH_ERROR,
        "BUDGET_EXCEEDED": FailureCategory.BUDGET_EXCEEDED,
        "HUMAN_INPUT_REQUIRED": FailureCategory.HUMAN_INPUT_REQUIRED,
    }

    @classmethod
    def classify(cls, exception_msg: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        msg = (exception_msg or "").lower()
        meta = metadata or {}
        kind = meta.get("kind", "").lower()

        # Prioridade por metadata explícito
        if kind in cls.CATEGORIES.values():
            return kind

        # 1. Crash explícito (OOM / SIGKILL / crash do processo) antes de socket/reset
        if "oom" in msg or "sigkill" in msg or "crash" in msg:
            return FailureCategory.WORKER_CRASH

        # 2. Tool timeout / tool falha antes de timeout genérico de provider
        if "tool timeout" in msg:
            return FailureCategory.TOOL_TRANSIENT

        # 3. Falha de dependência
        if "dependency missing" in msg or "unmet dependency" in msg or "parent task not completed" in msg or "dependency" in msg:
            return FailureCategory.DEPENDENCY_MISSING

        # 4. Budget exceeded: "token limit" e "max tokens" devem ser budget, não auth
        if "token limit" in msg or "max tokens" in msg or "budget" in msg or "cost limit" in msg or "runtime exceeded" in msg:
            return FailureCategory.BUDGET_EXCEEDED

        # 5. Provider / infra transitória
        if "429" in msg or "500" in msg or "502" in msg or "503" in msg or "timeout" in msg or "provider" in msg or "rate limit" in msg:
            return FailureCategory.PROVIDER_ERROR

        # 6. Auth error: só se tiver "auth", "bearer", "access token", "unauthorized", "forbidden", 401, 403
        if "auth" in msg or "bearer" in msg or "access token" in msg or "unauthorized" in msg or "forbidden" in msg or "401" in msg or "403" in msg:
            return FailureCategory.AUTH_ERROR

        # 7. Outras categorias
        if "review" in msg or "changes_requested" in msg or "rejected" in msg:
            return FailureCategory.REVIEW_REJECTION
        if "acceptance" in msg or "test failed" in msg or "pytest" in msg or "lsp diagnostic" in msg:
            return FailureCategory.ACCEPTANCE_FAILURE
        if "contract" in msg or "violation" in msg or "schema" in msg:
            return FailureCategory.PROTOCOL_VIOLATION
        if "capability" in msg or "unsupported capability" in msg:
            return FailureCategory.CAPABILITY_MISSING
        if "human" in msg or "approval required" in msg:
            return FailureCategory.HUMAN_INPUT_REQUIRED
        if "tool" in msg or "connection reset" in msg or "broken pipe" in msg:
            return FailureCategory.TOOL_TRANSIENT

        return FailureCategory.WORKER_CRASH

    @classmethod
    def retry_policy_for(cls, category: str) -> Dict[str, Any]:
        """Informa qual a ação e política de retry correspondente à categoria."""
        if category == FailureCategory.PROVIDER_ERROR:
            return {"action": "provider_failover", "increments_task_retry": False}
        if category == FailureCategory.TOOL_TRANSIENT:
            return {"action": "tool_retry", "increments_task_retry": False}
        if category == FailureCategory.REVIEW_REJECTION:
            return {"action": "rework", "increments_task_retry": False}
        if category == FailureCategory.ACCEPTANCE_FAILURE:
            return {"action": "rework", "increments_task_retry": True}
        if category == FailureCategory.PROTOCOL_VIOLATION:
            return {"action": "rework", "increments_task_retry": True}
        if category == FailureCategory.DEPENDENCY_MISSING:
            return {"action": "wait_for_dependency", "increments_task_retry": False}
        if category == FailureCategory.CAPABILITY_MISSING:
            return {"action": "request_capability", "increments_task_retry": False}
        if category == FailureCategory.HUMAN_INPUT_REQUIRED:
            return {"action": "wait_for_human", "increments_task_retry": False}
        if category == FailureCategory.WORKER_CRASH:
            return {"action": "new_run", "increments_task_retry": True}
        if category == FailureCategory.AUTH_ERROR:
            return {"action": "block_for_credentials", "increments_task_retry": False}
        if category == FailureCategory.BUDGET_EXCEEDED:
            return {"action": "terminate_budget", "increments_task_retry": False}
        return {"action": "escalate", "increments_task_retry": True}
