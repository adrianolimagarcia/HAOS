import asyncio
from unittest.mock import patch

from tools import mcp_tool_config


def test_mcp_a2a_defaults_to_existing_fallback():
    with patch("hermes_cli.config_effective.load_user_config_effective", return_value={"terminal": {}}):
        assert mcp_tool_config._mcp_a2a_settings() == (True, False)


def test_mcp_a2a_explicit_disable_and_required():
    cfg = {"terminal": {"mcp_a2a": {"enabled": False, "required": True}}}
    with patch("hermes_cli.config_effective.load_user_config_effective", return_value=cfg):
        assert mcp_tool_config._mcp_a2a_settings() == (False, True)
