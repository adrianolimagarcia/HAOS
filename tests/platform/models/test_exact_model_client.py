"""Tests for ExactModelClient (A6API primary + exact-model failover)."""

import json
import unittest
from typing import Any, Dict

from hermes.platform.models.circuit_breaker import CircuitBreaker
from hermes.platform.models.client import (
    ChatMessage,
    CompletionResult,
    ExactModelClient,
)
from hermes.platform.models.profiles import ModelIdentity, ModelProfile, ProviderRoute
from hermes.platform.models.provider_router import ModelRouteExhaustedException


class TestExactModelClient(unittest.TestCase):
    """Test suite validating real provider adapter behavior and exact-model failover."""

    def setUp(self):
        self.cb = CircuitBreaker()
        self.identity = ModelIdentity(family="deepseek-v3", variant="default")
        self.profile = ModelProfile(
            id="coding-primary",
            model_identity=self.identity,
            routes=[
                ProviderRoute(provider_id="a6api", provider_model_id="deepseek-v3", priority=1),
                ProviderRoute(provider_id="openrouter", provider_model_id="deepseek/deepseek-chat", priority=2),
            ],
            parameters={"temperature": 0.2, "max_tokens": 4096},
        )

    def test_primary_provider_success(self):
        """When A6API is healthy, it handles the request and returns typed CompletionResult."""
        calls = []

        def mock_transport(url: str, headers: Dict[str, str], data: bytes, timeout: float):
            calls.append((url, headers, json.loads(data.decode("utf-8"))))
            return 200, {
                "id": "chatcmpl-a6api-001",
                "choices": [{"message": {"role": "assistant", "content": "Hello from A6API!"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
            }

        client = ExactModelClient(circuit_breaker=self.cb, transport_fn=mock_transport)
        res = client.complete(self.profile, [ChatMessage("user", "Hello")])

        self.assertEqual(len(calls), 1)
        self.assertIn("api.a6api.com", calls[0][0])
        self.assertEqual(res.provider_id, "a6api")
        self.assertEqual(res.provider_model_id, "deepseek-v3")
        self.assertEqual(res.content, "Hello from A6API!")
        self.assertEqual(res.prompt_tokens, 12)
        self.assertEqual(res.completion_tokens, 5)

        # Circuit breaker should be CLOSED (healthy)
        key = self.cb.route_key("a6api", self.identity)
        self.assertFalse(self.cb.is_open(key))

    def test_exact_model_failover_on_429(self):
        """When A6API hits rate-limit (429), failover goes to OpenRouter for exact model."""
        calls = []

        def mock_transport(url: str, headers: Dict[str, str], data: bytes, timeout: float):
            parsed = json.loads(data.decode("utf-8"))
            calls.append((url, parsed["model"]))
            if "a6api" in url:
                # Rate limit from A6API
                return 429, {"error": {"message": "Rate limit exceeded on A6API"}}
            elif "openrouter" in url:
                # Success on OpenRouter
                return 200, {
                    "id": "chatcmpl-openrouter-001",
                    "choices": [{"message": {"role": "assistant", "content": "Hello from OpenRouter failover!"}}],
                    "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23},
                }
            return 500, {}

        client = ExactModelClient(circuit_breaker=self.cb, transport_fn=mock_transport)
        res = client.complete(self.profile, [ChatMessage("user", "Fix bug")])

        self.assertEqual(len(calls), 2)
        # First call was to A6API
        self.assertIn("a6api", calls[0][0])
        self.assertEqual(calls[0][1], "deepseek-v3")

        # Second call was to OpenRouter for the SAME model
        self.assertIn("openrouter", calls[1][0])
        self.assertEqual(calls[1][1], "deepseek/deepseek-chat")

        self.assertEqual(res.provider_id, "openrouter")
        self.assertEqual(res.content, "Hello from OpenRouter failover!")
        self.assertEqual(res.model_family, "deepseek-v3")

    def test_exhaustion_raises_without_degrading(self):
        """When all routes fail, ModelRouteExhaustedException is raised without model downgrade."""
        def all_fail_transport(url: str, headers: Dict[str, str], data: bytes, timeout: float):
            return 500, {"error": "Server error"}

        client = ExactModelClient(circuit_breaker=self.cb, transport_fn=all_fail_transport)

        with self.assertRaises(ModelRouteExhaustedException) as ctx:
            client.complete(self.profile, [ChatMessage("user", "Compute")])

        self.assertIn("deepseek-v3:default", str(ctx.exception))
        self.assertIn("failed or were open", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
