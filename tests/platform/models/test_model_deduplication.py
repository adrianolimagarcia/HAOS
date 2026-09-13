"""Test suite for Model Deduplication in /v1/models.

Validates:
1. Deduplication of models shared by multiple providers (e.g. gpt-5.6-luna shared by a6api and codex).
2. Case-insensitive HashSet filtering.
3. Provider-prefixed entries (a6api_... and codex_...) are preserved.
"""

import unittest
from hermes.platform.models.model_resolver import deduplicate_models


class TestModelDeduplication(unittest.TestCase):
    """Verifies case-insensitive HashSet model deduplication for /v1/models."""

    def test_deduplicate_shared_model_with_prefixes(self):
        input_models = [
            "gpt-5.6-luna",           # shared model from a6api
            "gpt-5.6-luna",           # duplicate from codex
            "GPT-5.6-LUNA",           # case variation
            "a6api_gpt-5.6-luna",     # prefixed provider route
            "codex_gpt-5.6-luna",     # prefixed provider route
            "deepseek-v4-flash",
            "a6api_deepseek-v4-flash",
        ]

        deduped = deduplicate_models(input_models)

        # Exact expected list preserving order of first appearance
        expected = [
            "gpt-5.6-luna",
            "a6api_gpt-5.6-luna",
            "codex_gpt-5.6-luna",
            "deepseek-v4-flash",
            "a6api_deepseek-v4-flash",
        ]

        self.assertEqual(deduped, expected)
        # Verify gpt-5.6-luna appears exactly once
        self.assertEqual(deduped.count("gpt-5.6-luna"), 1)
        # Verify prefixed variants are retained
        self.assertIn("a6api_gpt-5.6-luna", deduped)
        self.assertIn("codex_gpt-5.6-luna", deduped)

    def test_deduplicate_model_dictionaries(self):
        raw_models = [
            {"id": "gpt-5.6-luna", "root": "gpt-5.6-luna"},
            {"id": "GPT-5.6-LUNA", "root": "gpt-5.6-luna"},
            {"id": "a6api_gpt-5.6-luna", "root": "gpt-5.6-luna"},
            {"id": "codex_gpt-5.6-luna", "root": "gpt-5.6-luna"},
            {"id": "deepseek-v4-flash", "root": "deepseek-v4-flash"},
        ]

        deduped = deduplicate_models(raw_models)
        deduped_ids = [m["id"] for m in deduped]

        self.assertEqual(
            deduped_ids,
            ["gpt-5.6-luna", "a6api_gpt-5.6-luna", "codex_gpt-5.6-luna", "deepseek-v4-flash"]
        )


if __name__ == "__main__":
    unittest.main()
