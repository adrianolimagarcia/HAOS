"""Exact Model Client & Provider Transport Adapter (Phase 1 — Kernel Adapters).

Implements the execution transport for ExactModelRouter:
- Dispatches chat completion requests across provider venues (e.g. A6API, OpenRouter, DeepSeek).
- Strictly preserves ModelIdentity: if provider 1 (A6API) fails, fails over to provider 2 (OpenRouter)
  for the exact same model.
- Stdlib-only (urllib.request, json, time).
- Pluggable transport_fn seam for deterministic offline tests and live network execution.
- Updates CircuitBreaker on success/failure to prevent hammering failing providers.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from hermes.platform.models.circuit_breaker import CircuitBreaker
from hermes.platform.models.profiles import ModelProfile, ProviderRoute
from hermes.platform.models.provider_router import ExactModelRouter, ModelRouteExhaustedException


# Default provider API base endpoints
PROVIDER_BASE_URLS: Dict[str, str] = {
    "a6api": "https://api.a6api.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "deepseek-direct": "https://api.deepseek.com/v1",
}

# Environment variable names for API keys
PROVIDER_ENV_KEYS: Dict[str, str] = {
    "a6api": "A6API_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "deepseek-direct": "DEEPSEEK_API_KEY",
}


@dataclass
class ChatMessage:
    role: str
    content: str
    name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        return d


@dataclass
class CompletionResult:
    content: str
    model_family: str
    model_variant: str
    provider_id: str
    provider_model_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_sec: float = 0.0
    finish_reason: str = "stop"
    raw_response: Dict[str, Any] = field(default_factory=dict)


# Transport function signature: (url, headers, data_bytes, timeout) -> (status_code, body_dict)
TransportFn = Callable[[str, Dict[str, str], bytes, float], tuple[int, Dict[str, Any]]]


def default_http_transport(
    url: str,
    headers: Dict[str, str],
    data: bytes,
    timeout: float,
) -> tuple[int, Dict[str, Any]]:
    """Standard HTTP transport using Python stdlib urllib."""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body = json.loads(resp.read().decode("utf-8"))
            return status, body
    except urllib.error.HTTPError as exc:
        err_body: Dict[str, Any] = {}
        try:
            err_body = json.loads(exc.read().decode("utf-8"))
        except Exception:
            err_body = {"error": str(exc)}
        return exc.code, err_body
    except Exception as exc:
        return 599, {"error": str(exc)}


class ExactModelClient:
    """Dispatches completions honoring ExactModelRouter routes and failover."""

    def __init__(
        self,
        router: Optional[ExactModelRouter] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        transport_fn: Optional[TransportFn] = None,
        timeout_sec: float = 30.0,
    ):
        if circuit_breaker is not None:
            self.cb = circuit_breaker
        elif router is not None:
            self.cb = router.circuit_breaker
        else:
            self.cb = CircuitBreaker()

        self.router = router or ExactModelRouter(circuit_breaker=self.cb)
        self.transport_fn = transport_fn or default_http_transport
        self.timeout_sec = timeout_sec

    def _get_api_key(self, provider_id: str) -> str:
        env_var = PROVIDER_ENV_KEYS.get(provider_id, f"{provider_id.upper()}_API_KEY")
        return os.environ.get(env_var, "")

    def _get_base_url(self, provider_id: str) -> str:
        env_var = f"{provider_id.upper()}_BASE_URL"
        return os.environ.get(env_var, PROVIDER_BASE_URLS.get(provider_id, "https://api.openai.com/v1"))

    def complete(
        self,
        profile: ModelProfile,
        messages: List[ChatMessage | Dict[str, Any]],
        parameters_override: Optional[Dict[str, Any]] = None,
    ) -> CompletionResult:
        """Executes a chat completion attempting routes in priority order.

        If a route fails (429, 5xx, or network error), trips the circuit breaker
        and immediately tries the next route for the EXACT same model identity.
        """
        # Format messages
        formatted_messages = []
        for msg in messages:
            if isinstance(msg, ChatMessage):
                formatted_messages.append(msg.to_dict())
            else:
                formatted_messages.append(msg)

        params = dict(profile.parameters)
        if parameters_override:
            params.update(parameters_override)

        routes = self.router.ordered_routes(profile)
        if not routes:
            raise ModelRouteExhaustedException(f"Profile '{profile.id}' has no routes configured.")

        attempted_errors: List[str] = []

        for route in routes:
            cb_key = self.cb.route_key(route.provider_id, profile.model_identity)
            if self.cb.is_open(cb_key):
                attempted_errors.append(f"Provider {route.provider_id} circuit breaker is OPEN")
                continue

            base_url = self._get_base_url(route.provider_id).rstrip("/")
            endpoint = f"{base_url}/chat/completions"
            api_key = self._get_api_key(route.provider_id)

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}" if api_key else "",
            }
            if route.provider_id == "openrouter":
                headers["HTTP-Referer"] = "https://github.com/adrianolimagarcia/hermes-agent"
                headers["X-Title"] = "HAOS-Agent"

            payload = {
                "model": route.provider_model_id,
                "messages": formatted_messages,
                "temperature": params.get("temperature", 0.1),
            }
            if "max_tokens" in params:
                payload["max_tokens"] = params["max_tokens"]

            data_bytes = json.dumps(payload).encode("utf-8")
            start_t = time.time()

            try:
                status, body = self.transport_fn(endpoint, headers, data_bytes, self.timeout_sec)
                latency = time.time() - start_t

                if status == 200 and "choices" in body and body["choices"]:
                    # Success: record success to circuit breaker
                    self.cb.record_success(cb_key)
                    choice = body["choices"][0]
                    msg_obj = choice.get("message", {})
                    content = msg_obj.get("content", "")
                    if not content and "reasoning_content" in msg_obj and msg_obj.get("reasoning_content"):
                        # Fallback to reasoning_content if model depleted budget before emitting final content
                        content = msg_obj.get("reasoning_content")
                    finish_reason = choice.get("finish_reason", "stop")
                    usage = body.get("usage", {})

                    return CompletionResult(
                        content=content,
                        model_family=profile.model_identity.family,
                        model_variant=profile.model_identity.variant,
                        provider_id=route.provider_id,
                        provider_model_id=route.provider_model_id,
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0),
                        latency_sec=latency,
                        finish_reason=finish_reason,
                        raw_response=body,
                    )
                else:
                    # Failure response from provider
                    err_msg = body.get("error", f"HTTP {status}")
                    self.cb.record_failure(cb_key)
                    attempted_errors.append(f"{route.provider_id} returned HTTP {status}: {err_msg}")

            except Exception as exc:
                self.cb.record_failure(cb_key)
                attempted_errors.append(f"{route.provider_id} connection error: {exc}")

        # If all routes were attempted and failed, fail-closed without model degradation
        from hermes.platform.models.provider_router import _emit_route_exhausted
        _emit_route_exhausted({
            "model_family": profile.model_identity.family,
            "model_variant": profile.model_identity.variant,
            "exhausted_providers": [r.provider_id for r in routes],
            "timestamp": time.time(),
            "profile_id": profile.id,
            "errors": attempted_errors,
        })
        raise ModelRouteExhaustedException(
            f"All provider routes for model {profile.model_identity.family}:{profile.model_identity.variant} "
            f"failed or were open. Errors: {'; '.join(attempted_errors)}"
        )
