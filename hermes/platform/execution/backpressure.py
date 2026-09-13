"""ConcurrencyGuard / BackpressureController para execução de tarefas (HAOS v1.1 / Phase 2).

Controla concorrência determinística nos níveis:
1. Global (max_active_workers / max_global_concurrency)
2. Por provider (ex: A6API, OpenAI, Anthropic) para evitar 429
3. Por modelo ou route key (ex: gpt-4o, deepseek-v3, route_key composta)
4. Integração opcional com CircuitBreaker para rejeitar rotas abertas precocemente

Garante que se a capacidade estiver esgotada, a tarefa permanece intacta em READY
sem claim_lock e sem spawnar subprocessos ou abrir requisições que falharão.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Set, Union

from hermes.platform.models.circuit_breaker import CircuitBreaker


@dataclass(frozen=True)
class AdmissionDecision:
    """Resultado determinístico de uma checagem de admissão."""
    allowed: bool
    reason: str = ""
    global_active: int = 0
    provider_active: int = 0
    model_active: int = 0
    route_key: str = ""

    def __bool__(self) -> bool:
        return self.allowed


class ConcurrencyGuard:
    """
    Controlador determinístico de concorrência e backpressure.
    Rastreia capacidade e ocupação global, por provider e por modelo/rota.
    Thread-safe com locks reentrantes.
    """

    DEFAULT_PROVIDER_LIMITS: Dict[str, int] = {
        "a6api": 4,
        "openai": 4,
        "anthropic": 4,
        "fallback": 2,
    }

    def __init__(
        self,
        max_global_concurrency: Optional[int] = None,
        *,
        max_active_workers: Optional[int] = None,
        default_provider_limit: int = 2,
        provider_limits: Optional[Dict[str, int]] = None,
        model_limits: Optional[Dict[str, int]] = None,
        default_model_limit: Optional[int] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        # Suporta tanto max_global_concurrency quanto max_active_workers (default 8)
        global_concurrency = 8
        if max_global_concurrency is not None:
            global_concurrency = max_global_concurrency
        elif max_active_workers is not None:
            global_concurrency = max_active_workers

        self.max_global_concurrency = max(1, global_concurrency)
        self.max_active_workers = self.max_global_concurrency
        self.default_provider_limit = max(1, default_provider_limit)
        
        # Provider limits case-insensitive normalizados em lowercase
        self.provider_limits: Dict[str, int] = dict(self.DEFAULT_PROVIDER_LIMITS)
        if provider_limits:
            for k, v in provider_limits.items():
                self.provider_limits[k.lower()] = max(1, v)
        
        self.model_limits: Dict[str, int] = {}
        if model_limits:
            for k, v in model_limits.items():
                self.model_limits[k.lower()] = max(1, v)
                
        self.default_model_limit = default_model_limit
        self.circuit_breaker = circuit_breaker

        self._lock = threading.RLock()
        # active_tasks: task_id -> {provider_id, model_id, route_key, ref_count}
        self._active_tasks: Dict[str, Dict[str, Any]] = {}
        self._active_by_provider: Dict[str, int] = {}
        self._active_by_model: Dict[str, int] = {}
        self._active_by_route: Dict[str, int] = {}

    @property
    def active_workers_count(self) -> int:
        with self._lock:
            return len(self._active_tasks)

    # ------------------------------------------------------------------ #
    # Resolução de Limites
    # ------------------------------------------------------------------ #
    def get_provider_limit(self, provider_id: Optional[str]) -> int:
        if not provider_id:
            return self.max_active_workers
        p = provider_id.lower()
        if p in self.provider_limits:
            return self.provider_limits[p]
        if "fallback" in self.provider_limits:
            return self.provider_limits["fallback"]
        return self.default_provider_limit

    def get_model_limit(self, model_id: Optional[str]) -> Optional[int]:
        if not model_id:
            return None
        m = model_id.lower()
        if m in self.model_limits:
            return self.model_limits[m]
        return self.default_model_limit

    # ------------------------------------------------------------------ #
    # Checagem de Admissão (Pura / Read-only)
    # ------------------------------------------------------------------ #
    def check_admission(
        self,
        *,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        route_key: Optional[str] = None,
    ) -> AdmissionDecision:
        """Verifica se há capacidade para admitir uma tarefa com os requisitos dados.
        
        Não muta o estado interno. Seguro para chamadas antes do claim.
        """
        with self._lock:
            active_global = len(self._active_tasks)
            if active_global >= self.max_active_workers:
                return AdmissionDecision(
                    allowed=False,
                    reason="global_limit_reached",
                    global_active=active_global,
                )

            # 2. Rota no CircuitBreaker
            if self.circuit_breaker is not None and route_key:
                if self.circuit_breaker.is_open(route_key):
                    return AdmissionDecision(
                        allowed=False,
                        reason="circuit_breaker_open",
                        global_active=active_global,
                        route_key=route_key,
                    )

            # 3. Limite por Provider (se especificado)
            p_norm = provider_id.lower() if provider_id else None
            p_active = self._active_by_provider.get(p_norm, 0) if p_norm else 0
            if p_norm:
                p_limit = self.get_provider_limit(p_norm)
                if p_active >= p_limit:
                    return AdmissionDecision(
                        allowed=False,
                        reason=f"provider_limit_reached:{p_norm}",
                        global_active=active_global,
                        provider_active=p_active,
                    )

            # 4. Limite por Modelo (se especificado ou default)
            m_norm = model_id.lower() if model_id else None
            m_active = self._active_by_model.get(m_norm, 0) if m_norm else 0
            if m_norm or self.default_model_limit is not None:
                m_limit = self.get_model_limit(m_norm)
                if m_limit is not None and m_active >= m_limit:
                    return AdmissionDecision(
                        allowed=False,
                        reason=f"model_limit_reached:{m_norm or 'default'}",
                        global_active=active_global,
                        model_active=m_active,
                    )

            # 5. Limite específico por Route Key (se configurado)
            r_norm = route_key.lower() if route_key else None
            r_active = self._active_by_route.get(r_norm, 0) if r_norm else 0
            if r_norm:
                r_limit = self.model_limits.get(r_norm, self.default_model_limit)
                if r_limit is not None and r_active >= r_limit:
                    return AdmissionDecision(
                        allowed=False,
                        reason=f"route_limit_reached:{r_norm}",
                        global_active=active_global,
                        route_key=r_norm,
                    )

            return AdmissionDecision(
                allowed=True,
                reason="admitted",
                global_active=active_global,
                provider_active=p_active,
                model_active=m_active,
                route_key=route_key or "",
            )
            # 1. Teto global
            active_global = len(self._active_tasks)
            if active_global >= self.max_active_workers:
                return AdmissionDecision(
                    allowed=False,
                    reason="global_limit_reached",
                    global_active=active_global,
                )

            # 2. Circuit Breaker (se houver route_key e breaker configurado)
            if self.circuit_breaker is not None and route_key:
                if self.circuit_breaker.is_open(route_key):
                    return AdmissionDecision(
                        allowed=False,
                        reason="circuit_breaker_open",
                        global_active=active_global,
                        route_key=route_key,
                    )

            # 3. Limite por Provider
            p_norm = provider_id.lower() if provider_id else None
            p_active = self._active_by_provider.get(p_norm, 0) if p_norm else 0
            if p_norm:
                p_limit = self.get_provider_limit(p_norm)
                if p_active >= p_limit:
                    return AdmissionDecision(
                        allowed=False,
                        reason=f"provider_limit_reached:{p_norm}",
                        global_active=active_global,
                        provider_active=p_active,
                    )

            # 4. Limite por Modelo
            m_norm = model_id.lower() if model_id else None
            m_active = self._active_by_model.get(m_norm, 0) if m_norm else 0
            if m_norm:
                m_limit = self.get_model_limit(m_norm)
                if m_limit is not None and m_active >= m_limit:
                    return AdmissionDecision(
                        allowed=False,
                        reason=f"model_limit_reached:{m_norm}",
                        global_active=active_global,
                        model_active=m_active,
                    )

            # 5. Limite específico por Route Key (se configurado)
            r_norm = route_key.lower() if route_key else None
            r_active = self._active_by_route.get(r_norm, 0) if r_norm else 0
            if r_norm:
                r_limit = self.model_limits.get(r_norm)
                if r_limit is not None and r_active >= r_limit:
                    return AdmissionDecision(
                        allowed=False,
                        reason=f"route_limit_reached:{r_norm}",
                        global_active=active_global,
                        route_key=r_norm,
                    )

            return AdmissionDecision(
                allowed=True,
                reason="admitted",
                global_active=active_global,
                provider_active=p_active,
                model_active=m_active,
                route_key=route_key or "",
            )

    def can_acquire(
        self,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        route_key: Optional[str] = None,
    ) -> bool:
        """Compatibilidade direta com booleano can_acquire."""
        return self.check_admission(
            provider_id=provider_id,
            model_id=model_id,
            route_key=route_key,
        ).allowed

    # ------------------------------------------------------------------ #
    # Aquisição & Liberação de Capacidade
    # ------------------------------------------------------------------ #
    def acquire(
        self,
        task_id: str,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        *,
        route_key: Optional[str] = None,
    ) -> AdmissionDecision:
        """Tenta adquirir um slot de execução de forma atômica.
        
        Suporta reentrância segura (ref_count) para o mesmo task_id sem double-release race.
        Se bloqueado por backpressure, retorna AdmissionDecision(allowed=False, reason=...).
        """
        with self._lock:
            if task_id in self._active_tasks:
                # Idempotente: se já foi adquirido para esse task_id, retorna allowed=True
                # mas não incrementa ref_count cego para não mascarar vazamentos de release
                return AdmissionDecision(
                    allowed=True,
                    reason="already_acquired",
                    global_active=len(self._active_tasks),
                    provider_active=self._active_by_provider.get(self._active_tasks[task_id]["provider_id"] or "", 0),
                    model_active=self._active_by_model.get(self._active_tasks[task_id]["model_id"] or "", 0),
                    route_key=self._active_tasks[task_id]["route_key"] or "",
                )

            decision = self.check_admission(
                provider_id=provider_id,
                model_id=model_id,
                route_key=route_key,
            )
            if not decision.allowed:
                return decision

            p_norm = provider_id.lower() if provider_id else None
            m_norm = model_id.lower() if model_id else None
            r_norm = route_key.lower() if route_key else None

            self._active_tasks[task_id] = {
                "provider_id": p_norm,
                "model_id": m_norm,
                "route_key": r_norm,
                "ref_count": 1,
            }
            if p_norm:
                self._active_by_provider[p_norm] = self._active_by_provider.get(p_norm, 0) + 1
            if m_norm:
                self._active_by_model[m_norm] = self._active_by_model.get(m_norm, 0) + 1
            if r_norm:
                self._active_by_route[r_norm] = self._active_by_route.get(r_norm, 0) + 1

            return AdmissionDecision(
                allowed=True,
                reason="acquired",
                global_active=len(self._active_tasks),
                provider_active=self._active_by_provider.get(p_norm or "", 0),
                model_active=self._active_by_model.get(m_norm or "", 0),
                route_key=route_key or "",
            )

    def release(self, task_id: str, *, force: bool = False) -> None:
        """Libera o slot associado a task_id."""
        with self._lock:
            info = self._active_tasks.pop(task_id, None)
            if not info:
                return

            p = info.get("provider_id")
            if p and p in self._active_by_provider:
                self._active_by_provider[p] -= 1
                if self._active_by_provider[p] <= 0:
                    del self._active_by_provider[p]

            m = info.get("model_id")
            if m and m in self._active_by_model:
                self._active_by_model[m] -= 1
                if self._active_by_model[m] <= 0:
                    del self._active_by_model[m]

            r = info.get("route_key")
            if r and r in self._active_by_route:
                self._active_by_route[r] -= 1
                if self._active_by_route[r] <= 0:
                    del self._active_by_route[r]

    @contextmanager
    def lease(
        self,
        task_id: str,
        *,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        route_key: Optional[str] = None,
    ) -> Iterator[AdmissionDecision]:
        """Context manager de ciclo de vida seguro para acquire/release.
        
        Uso:
            with guard.lease(task_id, provider_id="openai") as decision:
                if not decision:
                    # capacidade esgotada, task permanece READY
                    return
                # executar worker
        """
        decision = self.acquire(
            task_id,
            provider_id=provider_id,
            model_id=model_id,
            route_key=route_key,
        )
        try:
            yield decision
        finally:
            if decision.allowed and decision.reason != "already_acquired":
                self.release(task_id)

    # ------------------------------------------------------------------ #
    # Observabilidade / Telemetria
    # ------------------------------------------------------------------ #
    def stats(self) -> Dict[str, Any]:
        """Retorna snapshot limpo das métricas de ocupação e limites."""
        with self._lock:
            active_count = len(self._active_tasks)
            return {
                "global": {
                    "active": active_count,
                    "max": self.max_global_concurrency,
                    "available": max(0, self.max_global_concurrency - active_count),
                },
                "providers": {
                    p: {
                        "active": self._active_by_provider.get(p, 0),
                        "limit": self.get_provider_limit(p),
                    }
                    for p in sorted(set(list(self.provider_limits.keys()) + list(self._active_by_provider.keys())))
                },
                "models": {
                    m: {
                        "active": self._active_by_model.get(m, 0),
                        "limit": self.get_model_limit(m),
                    }
                    for m in sorted(set(list(self.model_limits.keys()) + list(self._active_by_model.keys())))
                },
                "active_tasks": dict(self._active_tasks),
                "active_task_ids": list(self._active_tasks.keys()),
            }


# Alias BackpressureController -> ConcurrencyGuard
BackpressureController = ConcurrencyGuard
