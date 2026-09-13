"""Integration tests for ExactModelFailover per Posture (Passo 3).

Validates:
1. Posture-to-model resolution for architect, coder, and reviewer.
2. Rate limits or circuit-breaker trips on Provider A failover to Provider B
   while maintaining the exact same model identity.
3. ModelDegradationPreventedError is raised instead of silently degrading to a cheaper/weaker model.
4. Lane executor (_build_argv / HermesLaneExecutor / HermesCliLaneWorker) resolves model and provider
   via ExactModelFailoverRouter based on posture or ModelProfile.
"""

import unittest
from hermes.platform.models.circuit_breaker import CircuitBreaker
from hermes.platform.models.unified_fabric import (
    ExactModelFailoverRouter,
    ModelProfile,
    ModelIdentity,
    ProviderPriorityEntry,
    ModelDegradationPreventedError,
)
from hermes.platform.execution.lane_executor import (
    HermesCliLaneWorker,
    HermesLaneExecutor,
)


class TestExactFailoverDispatcherIntegration(unittest.TestCase):
    def setUp(self):
        self.circuit_breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)
        self.router = ExactModelFailoverRouter(
            circuit_breaker=self.circuit_breaker,
            rate_limit_tracker={},
        )

    def test_posture_to_model_resolution(self):
        """Test posture-to-model resolution for architect, coder, and reviewer."""
        # 1. Architect posture
        arch_profile = self.router.resolve_posture_profile("architect")
        self.assertIsNotNone(arch_profile)
        self.assertEqual(arch_profile.model_name, "claude-3-7-sonnet")
        self.assertEqual(arch_profile.model_identity.family, "claude-3-7-sonnet")
        arch_providers = [p.provider_id for p in arch_profile.ordered_providers()]
        self.assertEqual(arch_providers, ["anthropic", "openrouter", "bedrock"])

        # 2. Coder posture
        coder_profile = self.router.resolve_posture_profile("coder")
        self.assertIsNotNone(coder_profile)
        self.assertEqual(coder_profile.model_name, "deepseek-v3")
        self.assertEqual(coder_profile.model_identity.family, "deepseek-v3")
        coder_providers = [p.provider_id for p in coder_profile.ordered_providers()]
        self.assertEqual(coder_providers, ["deepseek", "openrouter"])

        # 3. Reviewer posture
        reviewer_profile = self.router.resolve_posture_profile("reviewer")
        self.assertIsNotNone(reviewer_profile)
        self.assertEqual(reviewer_profile.model_name, "claude-3-5-sonnet")
        self.assertEqual(reviewer_profile.model_identity.family, "claude-3-5-sonnet")
        rev_providers = [p.provider_id for p in reviewer_profile.ordered_providers()]
        self.assertEqual(rev_providers, ["anthropic", "openrouter", "bedrock"])

    def test_failover_on_rate_limit_maintains_exact_model(self):
        """Test rate limit on Provider A fails over to Provider B with exact same model identity."""
        arch_profile = self.router.resolve_posture_profile("architect")
        self.assertIsNotNone(arch_profile)

        # Initially routes to Anthropic (priority 1)
        decision1 = self.router.select_route_for_posture("architect")
        self.assertEqual(decision1.selected_provider_id, "anthropic")
        self.assertEqual(decision1.model_name, "claude-3-7-sonnet")
        self.assertEqual(decision1.selected_provider_model_id, "claude-3-7-sonnet")

        # Provider A hits rate limit
        self.router.record_failure("anthropic", arch_profile, is_rate_limit=True, rate_limit_cooldown=30.0)

        # Fails over to OpenRouter (priority 2), maintaining exact model identity
        decision2 = self.router.select_route_for_posture("architect")
        self.assertEqual(decision2.selected_provider_id, "openrouter")
        self.assertEqual(decision2.model_name, "claude-3-7-sonnet")
        self.assertEqual(decision2.selected_provider_model_id, "anthropic/claude-3.7-sonnet")
        self.assertIn("anthropic", decision2.bypassed_providers)

    def test_failover_on_circuit_breaker_maintains_exact_model(self):
        """Test circuit breaker trip on Provider A fails over to Provider B maintaining exact model."""
        coder_profile = self.router.resolve_posture_profile("coder")
        self.assertIsNotNone(coder_profile)

        # Initially DeepSeek direct
        decision1 = self.router.select_route_for_posture("coder")
        self.assertEqual(decision1.selected_provider_id, "deepseek")
        self.assertEqual(decision1.model_name, "deepseek-v3")
        self.assertEqual(decision1.selected_provider_model_id, "deepseek-chat")

        # Provider A trips circuit breaker (failure threshold = 2)
        self.router.record_failure("deepseek", coder_profile)
        self.router.record_failure("deepseek", coder_profile)

        # Failover to OpenRouter with exact model identity deepseek-v3
        decision2 = self.router.select_route_for_posture("coder")
        self.assertEqual(decision2.selected_provider_id, "openrouter")
        self.assertEqual(decision2.model_name, "deepseek-v3")
        self.assertEqual(decision2.selected_provider_model_id, "deepseek/deepseek-chat")
        self.assertIn("deepseek", decision2.bypassed_providers)

    def test_model_degradation_prevented_error_raised(self):
        """Test ModelDegradationPreventedError is raised when all providers for exact model fail."""
        coder_profile = self.router.resolve_posture_profile("coder")
        self.assertIsNotNone(coder_profile)

        # Trip deepseek
        self.router.record_failure("deepseek", coder_profile)
        self.router.record_failure("deepseek", coder_profile)

        # Trip openrouter
        self.router.record_failure("openrouter", coder_profile)
        self.router.record_failure("openrouter", coder_profile)

        # Both providers exhausted for deepseek-v3. Must raise ModelDegradationPreventedError,
        # strictly preventing silent downgrade/degradation.
        with self.assertRaises(ModelDegradationPreventedError) as ctx:
            self.router.select_route_for_posture("coder")

        self.assertIn("Silent model degradation strictly prevented", str(ctx.exception))
        self.assertIn("deepseek-v3", str(ctx.exception))

    def test_lane_executor_build_argv_resolves_posture_failover(self):
        """Test HermesLaneExecutor / HermesCliLaneWorker uses ExactModelFailoverRouter in _build_argv."""
        worker_arch = HermesLaneExecutor(hermes_command="hermes", failover_router=self.router)

        # 1. Spec with posture='architect'
        spec_arch = {"posture": "architect", "task_id": "T-1"}
        argv_arch = worker_arch._build_argv(spec_arch)
        self.assertIn("-m", argv_arch)
        model_idx = argv_arch.index("-m")
        self.assertEqual(argv_arch[model_idx + 1], "claude-3-7-sonnet")
        self.assertIn("--provider", argv_arch)
        prov_idx = argv_arch.index("--provider")
        self.assertEqual(argv_arch[prov_idx + 1], "anthropic")

        # 2. Trip anthropic rate limit
        arch_profile = self.router.resolve_posture_profile("architect")
        self.router.record_failure("anthropic", arch_profile, is_rate_limit=True, rate_limit_cooldown=60.0)

        # Now _build_argv fails over to openrouter without model degradation
        argv_arch_failover = worker_arch._build_argv(spec_arch)
        model_idx = argv_arch_failover.index("-m")
        self.assertEqual(argv_arch_failover[model_idx + 1], "anthropic/claude-3.7-sonnet")
        prov_idx = argv_arch_failover.index("--provider")
        self.assertEqual(argv_arch_failover[prov_idx + 1], "openrouter")

        # 3. Spec with posture='coder'
        spec_coder = {"posture": "coder", "task_id": "T-2"}
        argv_coder = worker_arch._build_argv(spec_coder)
        self.assertIn("-m", argv_coder)
        self.assertEqual(argv_coder[argv_coder.index("-m") + 1], "deepseek-chat")
        self.assertIn("--provider", argv_coder)
        self.assertEqual(argv_coder[argv_coder.index("--provider") + 1], "deepseek")

        # 4. Spec with posture='reviewer' (Anthropic is rate limited on arch, but reviewer has separate model or check)
        # Note: self.router._rate_limits has 'anthropic' from step 2! Let's clear or check
        self.router.record_success("anthropic", arch_profile)
        spec_rev = {"posture": "reviewer", "task_id": "T-3"}
        argv_rev = worker_arch._build_argv(spec_rev)
        self.assertIn("-m", argv_rev)
        self.assertEqual(argv_rev[argv_rev.index("-m") + 1], "claude-3-5-sonnet-latest")
        self.assertIn("--provider", argv_rev)
        self.assertEqual(argv_rev[argv_rev.index("--provider") + 1], "anthropic")

    def test_lane_executor_raises_model_degradation_prevented(self):
        """Test _build_argv propagates ModelDegradationPreventedError if all exact-model providers fail."""
        worker = HermesLaneExecutor(hermes_command="hermes", failover_router=self.router)
        coder_profile = self.router.resolve_posture_profile("coder")

        # Trip all providers for coder (deepseek, openrouter)
        self.router.record_failure("deepseek", coder_profile)
        self.router.record_failure("deepseek", coder_profile)
        self.router.record_failure("openrouter", coder_profile)
        self.router.record_failure("openrouter", coder_profile)

        spec_coder = {"posture": "coder", "task_id": "T-FAIL"}
        with self.assertRaises(ModelDegradationPreventedError):
            worker._build_argv(spec_coder)
