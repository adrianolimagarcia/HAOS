"""Tests for browser_network_requests — the HTTP-request view of the page.

The tool exists so an agent debugging "the button does nothing" can see the request
that actually failed instead of inferring it from the DOM. These tests pin the
behaviour contracts of that view: read-before-clear ordering, the filters, and the
redaction of credentials carried in headers.
"""

import json
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _requests_response(requests):
    return {"success": True, "data": {"requests": requests}, "error": None}


def _sample_requests():
    return [
        {
            "method": "GET",
            "url": "https://example.com/",
            "status": 200,
            "resourceType": "Document",
            "mimeType": "text/html",
            "headers": {"User-Agent": "UA", "Content-Type": "text/html"},
            "responseHeaders": {"server": "cloudflare", "content-type": "text/html"},
        },
        {
            "method": "GET",
            "url": "https://example.com/api/user",
            "status": 500,
            "resourceType": "Fetch",
            "mimeType": "application/json",
            "headers": {"Authorization": "Bearer opaque-token-not-a-known-shape"},
            "responseHeaders": {"content-type": "application/json"},
        },
        {
            "method": "POST",
            "url": "https://example.com/api/login",
            "status": 401,
            "resourceType": "XHR",
            "mimeType": "application/json",
            "headers": {"Cookie": "sessionid=fake-session-value", "X-API-Key": "fake-key-value"},
            "responseHeaders": {"Set-Cookie": "session=fake-session-value; HttpOnly"},
        },
    ]


class TestBrowserNetworkRequests:
    def test_returns_method_url_and_status(self):
        from tools.browser_tool import browser_network_requests

        with patch(
            "tools.browser_tool_session._run_browser_command",
            return_value=_requests_response(_sample_requests()),
        ):
            result = json.loads(browser_network_requests(task_id="test"))

        assert result["success"] is True
        assert result["total_requests"] == 3
        assert result["requests"][0]["method"] == "GET"
        assert result["requests"][0]["url"] == "https://example.com/"
        assert result["requests"][0]["status"] == 200
        assert result["requests"][0]["resource_type"] == "Document"
        assert result["requests"][1]["status"] == 500

    def test_only_failures_keeps_status_at_or_above_400(self):
        from tools.browser_tool import browser_network_requests

        with patch(
            "tools.browser_tool_session._run_browser_command",
            return_value=_requests_response(_sample_requests()),
        ):
            result = json.loads(browser_network_requests(only_failures=True, task_id="test"))

        assert result["total_requests"] == 2
        assert [r["status"] for r in result["requests"]] == [500, 401]
        # The unfiltered count stays visible so the agent knows work was hidden.
        assert result["requests_seen"] == 3

    def test_filter_pattern_reaches_the_cli(self):
        from tools.browser_tool import browser_network_requests

        with patch(
            "tools.browser_tool_session._run_browser_command",
            return_value=_requests_response([]),
        ) as mock_cmd:
            browser_network_requests(filter_pattern="/api/", task_id="test")

        assert mock_cmd.call_args_list[0][0] == ("test", "network", ["requests", "--filter", "/api/"])

    def test_clear_reads_before_clearing(self):
        """``--clear`` returns no requests, so clearing first would lose the data."""
        from tools.browser_tool import browser_network_requests

        with patch("tools.browser_tool_session._run_browser_command") as mock_cmd:
            mock_cmd.side_effect = [
                _requests_response(_sample_requests()),
                {"success": True, "data": {"cleared": True}},
            ]
            result = json.loads(browser_network_requests(clear=True, task_id="test"))

        calls = mock_cmd.call_args_list
        assert calls[0][0] == ("test", "network", ["requests"])
        assert calls[1][0] == ("test", "network", ["requests", "--clear"])
        # The read still produced data, and the clear is reported.
        assert result["total_requests"] == 3
        assert result["cleared"] is True

    def test_does_not_clear_when_not_requested(self):
        from tools.browser_tool import browser_network_requests

        with patch(
            "tools.browser_tool_session._run_browser_command",
            return_value=_requests_response([]),
        ) as mock_cmd:
            browser_network_requests(task_id="test")

        assert mock_cmd.call_count == 1

    def test_headers_are_opt_in(self):
        from tools.browser_tool import browser_network_requests

        with patch(
            "tools.browser_tool_session._run_browser_command",
            return_value=_requests_response(_sample_requests()),
        ):
            compact = json.loads(browser_network_requests(task_id="test"))
            detailed = json.loads(browser_network_requests(include_headers=True, task_id="test"))

        assert "headers" not in compact["requests"][0]
        assert "response_headers" not in compact["requests"][0]
        assert detailed["requests"][0]["headers"]["User-Agent"] == "UA"
        assert detailed["requests"][0]["response_headers"]["server"] == "cloudflare"

    def test_credential_headers_are_blanked_by_name(self):
        """The generic text redactor misses opaque tokens and Cookie/Set-Cookie, so the
        header NAME decides — otherwise a live session cookie reaches the model."""
        from tools.browser_tool import browser_network_requests

        with patch(
            "tools.browser_tool_session._run_browser_command",
            return_value=_requests_response(_sample_requests()),
        ):
            result = json.loads(browser_network_requests(include_headers=True, task_id="test"))

        serialized = json.dumps(result)
        assert "opaque-token-not-a-known-shape" not in serialized
        assert "fake-session-value" not in serialized
        assert "fake-key-value" not in serialized

        assert result["requests"][1]["headers"]["Authorization"] == "***"
        assert result["requests"][2]["headers"]["Cookie"] == "***"
        assert result["requests"][2]["headers"]["X-API-Key"] == "***"
        assert result["requests"][2]["response_headers"]["Set-Cookie"] == "***"
        # Debugging signal survives: harmless headers are untouched.
        assert result["requests"][0]["headers"]["Content-Type"] == "text/html"
        assert result["requests"][0]["response_headers"]["server"] == "cloudflare"

    def test_redaction_helper_is_shape_and_case_insensitive(self):
        from tools.browser_tool import _redact_header_values

        redacted = _redact_header_values({
            "AUTHORIZATION": "Basic opaque-blob",
            "cookie": "a=b",
            "X-Api-Key": "k",
            "x-trace-id": "keep-me",
        })

        assert redacted["AUTHORIZATION"] == "***"
        assert redacted["cookie"] == "***"
        assert redacted["X-Api-Key"] == "***"
        assert redacted["x-trace-id"] == "keep-me"
        # A non-dict is passed through rather than raising.
        assert _redact_header_values(None) is None

    def test_cli_failure_surfaces_an_error(self):
        from tools.browser_tool import browser_network_requests

        with patch(
            "tools.browser_tool_session._run_browser_command",
            return_value={"success": False, "error": "no session"},
        ):
            result = json.loads(browser_network_requests(task_id="test"))

        assert result["success"] is False
        assert "no session" in result["error"]

    def test_camofox_mode_reports_unavailability(self):
        """Camofox has no network command — say so instead of returning an empty list."""
        from tools.browser_tool import browser_network_requests

        with patch("tools.browser_tool._is_camofox_mode", return_value=True):
            with patch("tools.browser_tool_session._run_browser_command") as mock_cmd:
                result = json.loads(browser_network_requests(task_id="test"))

        assert result["success"] is False
        assert "Camofox" in result["error"]
        mock_cmd.assert_not_called()

    def test_malformed_entries_are_skipped(self):
        from tools.browser_tool import browser_network_requests

        with patch(
            "tools.browser_tool_session._run_browser_command",
            return_value=_requests_response(["not-a-dict", _sample_requests()[0]]),
        ):
            result = json.loads(browser_network_requests(task_id="test"))

        assert result["total_requests"] == 1
        assert result["requests"][0]["status"] == 200
