"""Tests for PostureCapabilitySandboxing."""

import unittest
from hermes.platform.capabilities.sandboxing import PostureCapabilitySandboxing


class TestPostureCapabilitySandboxing(unittest.TestCase):
    def setUp(self):
        self.all_tools = [
            {"name": "read_file", "description": "Read file"},
            {"name": "write_file", "description": "Write file"},
            {"name": "edit_file", "description": "Edit file"},
            {"name": "terminal", "description": "Run terminal command"},
            {"name": "execute_code", "description": "Execute code directly"},
            {"name": "context_expand", "description": "Expand context item"},
            {"name": "run_tests", "description": "Execute test suite"},
        ]

    def test_reviewer_sandboxing_strips_mutation_and_terminal(self):
        filtered = PostureCapabilitySandboxing.filter_tools_for_posture(
            self.all_tools, posture_name="reviewer"
        )
        names = [t["name"] for t in filtered]
        self.assertIn("read_file", names)
        self.assertIn("context_expand", names)
        self.assertIn("run_tests", names)
        # Stritamente proibidos para reviewer
        self.assertNotIn("write_file", names)
        self.assertNotIn("edit_file", names)
        self.assertNotIn("terminal", names)
        self.assertNotIn("execute_code", names)

    def test_coder_retains_coding_tools(self):
        filtered = PostureCapabilitySandboxing.filter_tools_for_posture(
            self.all_tools, posture_name="coder"
        )
        names = [t["name"] for t in filtered]
        self.assertIn("read_file", names)
        self.assertIn("write_file", names)
        self.assertIn("edit_file", names)
        self.assertIn("terminal", names)
        self.assertIn("execute_code", names)

    def test_architect_sandboxing_strips_destructive_code_execution(self):
        filtered = PostureCapabilitySandboxing.filter_tools_for_posture(
            self.all_tools, posture_name="architect"
        )
        names = [t["name"] for t in filtered]
        self.assertIn("read_file", names)
        self.assertIn("context_expand", names)
        self.assertNotIn("execute_code", names)
        self.assertNotIn("write_file", names)


if __name__ == "__main__":
    unittest.main()
