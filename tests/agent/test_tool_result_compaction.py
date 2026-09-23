"""Unit tests for ToolResultCompaction (Poda Cirúrgica de Saídas de Ferramentas / Camada A).

Tests verify:
1. Measurable reduction of tokens and characters.
2. Strict preservation of the protected tail (protect_last_n / protect_tail_tokens).
3. Strict preservation of call_id / tool_call_id and schema integrity (role='tool', alternation).
4. Deterministic observation markers for inspection tools (read_file, search_files, web_extract, web_search)
   and execution tools (terminal, execute_code).
5. ToolResultCompaction class and instance methods on ContextCompressor.
"""

import json
from unittest.mock import patch
import pytest

from agent.context_compressor import (
    ContextCompressor,
    ToolResultCompaction,
    compact_tool_results,
    prune_stale_tool_observations,
    mask_stale_observation,
)


def _build_test_conversation():
    """Build a conversation with older inspection/execution tool turns and a protected tail."""
    return [
        {"role": "system", "content": "You are Hermes."},
        {"role": "user", "content": "Check the codebase and run tests."},
        # 1. read_file call + big result (old turn)
        {
            "role": "assistant",
            "content": "Reading file...",
            "tool_calls": [
                {
                    "id": "call_read_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "/workspace/main.py"})},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_read_1",
            "content": "def main():\n" + ("    print('hello world')\n" * 200),  # ~5000 chars
        },
        # 2. search_files call + big result (old turn)
        {
            "role": "assistant",
            "content": "Searching files...",
            "tool_calls": [
                {
                    "id": "call_search_1",
                    "type": "function",
                    "function": {
                        "name": "search_files",
                        "arguments": json.dumps({"pattern": "TODO", "path": ".", "target": "content"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_search_1",
            "content": json.dumps({"matches": [{"file": f"file_{i}.py", "line": i} for i in range(100)]}),
        },
        # 3. web_extract call + big result (old turn)
        {
            "role": "assistant",
            "content": "Extracting docs...",
            "tool_calls": [
                {
                    "id": "call_web_1",
                    "type": "function",
                    "function": {
                        "name": "web_extract",
                        "arguments": json.dumps({"urls": ["https://example.com/docs", "https://example.com/api"]}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_web_1",
            "content": "# Documentation\n" + ("Important documentation paragraph.\n" * 150),
        },
        # 4. terminal call + big result (old turn)
        {
            "role": "assistant",
            "content": "Running tests...",
            "tool_calls": [
                {
                    "id": "call_term_1",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": json.dumps({"command": "pytest tests/ -v"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_term_1",
            "content": ("test_step passed\n" * 100) + '{"exit_code": 0, "output": "all passed"}',
        },
        # 5. execute_code call + big result (old turn)
        {
            "role": "assistant",
            "content": "Executing python snippet...",
            "tool_calls": [
                {
                    "id": "call_code_1",
                    "type": "function",
                    "function": {
                        "name": "execute_code",
                        "arguments": json.dumps({"code": "import sys\nprint('running snippet')"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_code_1",
            "content": ("snippet output line\n" * 80) + "Finished successfully.",
        },
        # Protected tail (last 4 messages)
        {"role": "assistant", "content": "I finished the preliminary checks. Now let me do the final write."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_tail_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": json.dumps({"path": "/workspace/final.py"})},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_tail_1",
            "content": "FINAL_IMPORTANT_DATA = " + ("9999," * 100),
        },
        {"role": "assistant", "content": "Everything is ready."},
    ]


class TestToolResultCompaction:
    """Tests for ToolResultCompaction module and helpers."""

    def test_mask_stale_observation_read_file(self):
        masked = mask_stale_observation(
            "read_file",
            {"path": "foo/bar.py"},
            "x" * 2000,
        )
        assert "[Observation masked: read_file(path='foo/bar.py')" in masked
        assert "2,000 chars omitted" in masked

    def test_mask_stale_observation_terminal(self):
        masked = mask_stale_observation(
            "terminal",
            {"command": "pytest -v"},
            'output line 1\noutput line 2\n{"exit_code": 0}',
        )
        assert "[Observation masked: terminal('pytest -v')" in masked
        assert "exit 0" in masked

    def test_mask_stale_observation_search_files(self):
        masked = mask_stale_observation(
            "search_files",
            {"pattern": "def test", "path": "tests", "target": "content"},
            "result payload" * 100,
        )
        assert "[Observation masked: search_files(pattern='def test', target='content', path='tests')" in masked

    def test_mask_stale_observation_web_extract(self):
        masked = mask_stale_observation(
            "web_extract",
            {"urls": ["https://docs.pytest.org", "https://docs.python.org"]},
            "documentation body" * 200,
        )
        assert "[Observation masked: web_extract(urls=['https://docs.pytest.org (+1 more)'])" in masked

    def test_compact_tool_results_reduces_chars_and_preserves_schema(self):
        messages = _build_test_conversation()
        chars_before = sum(len(str(m.get("content") or "")) for m in messages)

        # Compact with protect_last_n=4
        compacted, pruned_count = ToolResultCompaction.compact_tool_results(
            messages, protect_last_n=4, min_prune_chars=100,
        )

        chars_after = sum(len(str(m.get("content") or "")) for m in compacted)

        assert pruned_count == 5, f"Expected 5 pruned observations, got {pruned_count}"
        assert chars_after < chars_before * 0.3, "Expected > 70% reduction in characters"
        assert len(compacted) == len(messages), "Total message count must be strictly preserved"

        # Check strict preservation of role, tool_call_id, and alternation
        for orig, comp in zip(messages, compacted):
            assert orig["role"] == comp["role"], "Role must match"
            if orig.get("role") == "tool":
                assert orig["tool_call_id"] == comp["tool_call_id"], "tool_call_id must match strictly"

    def test_tail_protection_protect_last_n(self):
        messages = _build_test_conversation()
        tail_tool_orig_content = messages[-2]["content"]

        compacted, _ = compact_tool_results(messages, protect_last_n=4, min_prune_chars=100)

        # The tool result inside the tail (messages[-2]) must NOT be compacted
        assert compacted[-2]["content"] == tail_tool_orig_content
        assert "FINAL_IMPORTANT_DATA" in compacted[-2]["content"]

    def test_convenience_functions_and_aliases(self):
        messages = _build_test_conversation()

        res1, count1 = compact_tool_results(messages, protect_last_n=4, min_prune_chars=100)
        res2, count2 = prune_stale_tool_observations(messages, protect_last_n=4, min_prune_chars=100)

        assert count1 == count2
        assert [m["content"] for m in res1] == [m["content"] for m in res2]

    def test_compressor_instance_methods(self):
        with patch("agent.context_compressor.get_model_context_length", return_value=1_000_000):
            compressor = ContextCompressor(model="test", quiet_mode=True, protect_last_n=4)

        messages = _build_test_conversation()
        compacted, count = compressor.compact_tool_results(messages, min_prune_chars=100)

        assert count == 5
        # Tail tool result preserved
        assert "FINAL_IMPORTANT_DATA" in compacted[-2]["content"]
        # Old read_file tool result pruned
        assert "[Observation masked: read_file" in compacted[3]["content"]

    def test_no_op_when_all_within_protected_tail(self):
        short_convo = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        compacted, count = compact_tool_results(short_convo, protect_last_n=5)
        assert count == 0
        assert compacted == short_convo
