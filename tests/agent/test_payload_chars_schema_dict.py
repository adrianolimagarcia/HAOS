"""Regression test for TypeError: unhashable type: 'dict' in estimate_request_context_tokens."""

import unittest
from agent.chat_completion_helpers import estimate_request_context_tokens


class TestPayloadCharsSchemaDict(unittest.TestCase):
    def test_payload_chars_with_nested_dict_type_key(self):
        """Verify that a schema or payload having a dict under key 'type' does not raise TypeError."""
        payload = {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "system", "content": "You are HAOS."},
                {"role": "user", "content": "Hello"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "example_tool",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "description": "Type of widget",
                                },
                                "nested": {
                                    "type": {
                                        "inner": "dictionary",
                                    }
                                }
                            }
                        }
                    }
                }
            ]
        }
        tokens = estimate_request_context_tokens(payload)
        self.assertGreater(tokens, 0)


if __name__ == "__main__":
    unittest.main()
