"""Tests for Google Jules Skill and jules_worker.py CLI helper."""

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Carrega o script jules_worker.py dinamicamente
SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "skills" / "autonomous-ai-agents" / "google-jules" / "scripts" / "jules_worker.py"
spec = importlib.util.spec_from_file_location("jules_worker", SCRIPT_PATH)
jules_worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jules_worker)


class TestGoogleJulesSkill(unittest.TestCase):
    def test_repo_slug_parsing_https(self):
        with patch.object(jules_worker, "run_cmd", return_value="https://github.com/adrianolimagarcia/hermes-agent.git"):
            slug = jules_worker.get_github_repo_slug()
            self.assertEqual(slug, "adrianolimagarcia/hermes-agent")

    def test_repo_slug_parsing_ssh(self):
        with patch.object(jules_worker, "run_cmd", return_value="git@github.com:adrianolimagarcia/hermes-agent.git"):
            slug = jules_worker.get_github_repo_slug()
            self.assertEqual(slug, "adrianolimagarcia/hermes-agent")

    def test_get_api_key_from_env(self):
        with patch.dict(os.environ, {"JULES_API_KEY": "test-key-12345"}):
            self.assertEqual(jules_worker.get_api_key(), "test-key-12345")

    @patch("urllib.request.urlopen")
    def test_start_jules_task(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"name": "sessions/sess-99"}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        name = jules_worker.start_jules_task(
            prompt="Refactor auth",
            branch="jules/test-branch",
            api_key="key-abc",
            repo_slug="user/repo",
        )
        self.assertEqual(name, "sessions/sess-99")

    @patch("urllib.request.urlopen")
    def test_get_session_status(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "name": "sessions/sess-99",
            "state": "SUCCEEDED",
        }).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        data = jules_worker.get_session_status("sessions/sess-99", api_key="key-abc")
        self.assertEqual(data["state"], "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
