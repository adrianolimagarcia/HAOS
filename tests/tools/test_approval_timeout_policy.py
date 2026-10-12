"""Tests for approval timeout policy and read-only fallback on approval expiry."""

from unittest.mock import patch
import pytest

from tools import approval_context
from tools.approval import _human_decision, _COMMAND_GATE


class TestApprovalTimeoutPolicyConfig:
    def test_default_timeout_policy_is_abort(self):
        with patch("tools.approval_context._get_approval_config", return_value={}):
            assert approval_context._get_approval_timeout_policy() == "abort"

    def test_proceed_read_only_policy_parsed(self):
        with patch("tools.approval_context._get_approval_config", return_value={"timeout_policy": "proceed_read_only"}):
            assert approval_context._get_approval_timeout_policy() == "proceed_read_only"

    def test_case_and_whitespace_normalized(self):
        with patch("tools.approval_context._get_approval_config", return_value={"timeout_policy": "  PROCEED_READ_ONLY  "}):
            assert approval_context._get_approval_timeout_policy() == "proceed_read_only"

    def test_invalid_policy_falls_back_to_abort(self):
        with patch("tools.approval_context._get_approval_config", return_value={"timeout_policy": "yolo"}):
            assert approval_context._get_approval_timeout_policy() == "abort"


class TestOperationSeverityClassification:
    @pytest.mark.parametrize("cmd,desc", [
        ("rm -rf /tmp/data", "remove folder"),
        ("rm file.txt", "remove file"),
        ("shred -u secret.txt", "shred file"),
        ("dd if=/dev/zero of=/dev/sda", "wipe disk"),
        ("mkfs.ext4 /dev/sdb1", "format partition"),
        ("kill -9 1234", "kill process"),
        ("pkill -f node", "terminate node processes"),
        ("reboot", "reboot system"),
        ("shutdown -h now", "shutdown system"),
        ("chmod 777 /etc/passwd", "modify permissions"),
        ("chown root:root /var", "change ownership"),
        ("systemctl stop nginx", "stop service"),
        ("git push origin main --force", "force push"),
        ("git reset --hard HEAD~1", "hard reset"),
        ("echo foo > /etc/resolv.conf", "overwrite file"),
    ])
    def test_destructive_operations(self, cmd, desc):
        assert approval_context._is_destructive_operation(command=cmd, description=desc) is True
        assert approval_context._get_operation_severity(command=cmd, description=desc) == "destructive"

    @pytest.mark.parametrize("cmd,desc", [
        ("ls -la", "list directory contents"),
        ("cat /var/log/syslog", "read syslog"),
        ("grep -rn 'error' .", "search error in files"),
        ("ps aux", "list running processes"),
        ("df -h", "show disk space"),
        ("du -sh *", "check folder sizes"),
        ("git status", "check git status"),
        ("git log -n 5", "view commit history"),
        ("uname -a", "check kernel version"),
        ("systemctl status nginx", "view service status"),
    ])
    def test_read_only_diagnostic_operations(self, cmd, desc):
        assert approval_context._is_destructive_operation(command=cmd, description=desc) is False
        assert approval_context._get_operation_severity(command=cmd, description=desc) == "read_only"


class TestHumanDecisionTimeoutPolicy:
    def test_timeout_with_abort_policy_blocks_read_only_command(self):
        with patch("tools.approval._gateway_notify_cb", return_value=lambda x: None), \
             patch("tools.approval._await_gateway_decision", return_value={"resolved": False, "choice": None}), \
             patch("tools.approval_context._get_approval_config", return_value={"timeout_policy": "abort"}):
            res = _human_decision(
                _COMMAND_GATE,
                command="ls -la",
                description="list directory",
                pattern_key="ls",
                pattern_keys=["ls"],
                warnings=[],
                session_key="test_session",
                approval_callback=None,
                is_cli=False,
                is_gateway=True,
                is_ask=False,
            )
            assert res["approved"] is False
            assert res["outcome"] == "timeout"

    def test_timeout_with_proceed_read_only_allows_read_only_command(self):
        with patch("tools.approval._gateway_notify_cb", return_value=lambda x: None), \
             patch("tools.approval._await_gateway_decision", return_value={"resolved": False, "choice": None}), \
             patch("tools.approval_context._get_approval_config", return_value={"timeout_policy": "proceed_read_only"}):
            res = _human_decision(
                _COMMAND_GATE,
                command="cat /var/log/syslog",
                description="read syslog",
                pattern_key="cat",
                pattern_keys=["cat"],
                warnings=[],
                session_key="test_session",
                approval_callback=None,
                is_cli=False,
                is_gateway=True,
                is_ask=False,
            )
            assert res["approved"] is True
            assert res["proceed_read_only"] is True
            assert res["severity"] == "read_only"

    def test_timeout_with_proceed_read_only_still_blocks_destructive_command(self):
        with patch("tools.approval._gateway_notify_cb", return_value=lambda x: None), \
             patch("tools.approval._await_gateway_decision", return_value={"resolved": False, "choice": None}), \
             patch("tools.approval_context._get_approval_config", return_value={"timeout_policy": "proceed_read_only"}):
            res = _human_decision(
                _COMMAND_GATE,
                command="rm -rf /tmp/data",
                description="delete data directory",
                pattern_key="rm",
                pattern_keys=["rm"],
                warnings=[],
                session_key="test_session",
                approval_callback=None,
                is_cli=False,
                is_gateway=True,
                is_ask=False,
            )
            assert res["approved"] is False
            assert res["outcome"] == "timeout"
            assert "Destructive operations cannot proceed on timeout" in res["message"]
