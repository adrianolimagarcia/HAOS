import os
import tempfile
import unittest
from pathlib import Path

from hermes.platform.execution.backpressure import ConcurrencyGuard, BackpressureController, AdmissionDecision
from hermes.platform.models.circuit_breaker import CircuitBreaker


class TestConcurrencyGuard(unittest.TestCase):
    def setUp(self):
        self.guard = ConcurrencyGuard(
            max_active_workers=3,
            provider_limits={"a6api": 2, "openai": 1},
            model_limits={"gpt-4o": 1},
            default_provider_limit=1,
        )

    def test_global_concurrency_limit(self):
        # 3 slots globais
        d1 = self.guard.acquire("T-1", provider_id="a6api")
        self.assertTrue(d1.allowed)
        d2 = self.guard.acquire("T-2", provider_id="a6api")
        self.assertTrue(d2.allowed)
        d3 = self.guard.acquire("T-3", provider_id="anthropic")
        self.assertTrue(d3.allowed)

        # Quarto deve ser rejeitado por limite global
        d4 = self.guard.acquire("T-4", provider_id="anthropic")
        self.assertFalse(d4.allowed)
        self.assertEqual(d4.reason, "global_limit_reached")

        # Ao liberar T-1, slot abre
        self.guard.release("T-1")
        d4_retry = self.guard.acquire("T-4", provider_id="anthropic")
        self.assertTrue(d4_retry.allowed)

    def test_provider_concurrency_limit(self):
        # openai limit = 1
        d1 = self.guard.acquire("T-1", provider_id="openai")
        self.assertTrue(d1.allowed)

        # Outro openai rejeitado por provider_limit
        d2 = self.guard.acquire("T-2", provider_id="openai")
        self.assertFalse(d2.allowed)
        self.assertEqual(d2.reason, "provider_limit_reached:openai")

        # Outro provider ainda pode adquirir (global não estourou)
        d3 = self.guard.acquire("T-3", provider_id="a6api")
        self.assertTrue(d3.allowed)

    def test_model_concurrency_limit(self):
        # gpt-4o limit = 1
        d1 = self.guard.acquire("T-1", provider_id="a6api", model_id="gpt-4o")
        self.assertTrue(d1.allowed)

        d2 = self.guard.acquire("T-2", provider_id="a6api", model_id="gpt-4o")
        self.assertFalse(d2.allowed)
        self.assertEqual(d2.reason, "model_limit_reached:gpt-4o")

    def test_circuit_breaker_integration(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        guard = ConcurrencyGuard(max_active_workers=5, circuit_breaker=cb)

        route_key = "deepseek:v3:latest:a6api"
        # Inicialmente circuit está fechado
        d1 = guard.check_admission(provider_id="a6api", route_key=route_key)
        self.assertTrue(d1.allowed)

        # Tripping circuit
        cb.record_failure(route_key)
        self.assertTrue(cb.is_open(route_key))

        # Agora guard deve rejeitar
        d2 = guard.check_admission(provider_id="a6api", route_key=route_key)
        self.assertFalse(d2.allowed)
        self.assertEqual(d2.reason, "circuit_breaker_open")

    def test_lease_context_manager(self):
        guard = ConcurrencyGuard(max_active_workers=1)

        with guard.lease("T-1", provider_id="a6api") as d1:
            self.assertTrue(d1.allowed)
            self.assertEqual(guard.stats()["global"]["active"], 1)

            # Dentro do context manager, outro acquire falha
            d2 = guard.acquire("T-2", provider_id="a6api")
            self.assertFalse(d2.allowed)

        # Fora do context manager, T-1 foi liberado automaticamente
        self.assertEqual(guard.stats()["global"]["active"], 0)
        d2_retry = guard.acquire("T-2", provider_id="a6api")
        self.assertTrue(d2_retry.allowed)
