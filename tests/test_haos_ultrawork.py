"""Contract tests for HAOS Ultrawork Mode (OmO-inspired)."""

import argparse
import os
from unittest.mock import patch
from agent.prompt_builder import build_environment_hints
from hermes_cli._parser import build_top_level_parser
from hermes_cli.kanban_parser import build_parser as build_kanban_subparser


def test_ultrawork_prompt_injection():
    with patch.dict(os.environ, {"HAOS_ULTRAWORK_MODE": "1"}):
        hints = build_environment_hints()
        assert "ULTRAWORK MODE ACTIVE" in hints
        assert "automated tests pass cleanly" in hints

    with patch.dict(os.environ, {"HAOS_ULTRAWORK_MODE": "0"}, clear=True):
        hints_clean = build_environment_hints()
        assert "ULTRAWORK MODE ACTIVE" not in hints_clean


def test_cli_parser_ultrawork_flag():
    parser, _, chat_parser = build_top_level_parser()

    # Chat subparser flag
    args = chat_parser.parse_args(["-u", "-q", "Build feature"])
    assert getattr(args, "ultrawork", False) is True

    # Long flag
    args_long = chat_parser.parse_args(["--ultrawork", "-q", "Build feature"])
    assert getattr(args_long, "ultrawork", False) is True


def test_kanban_parser_ultrawork_flag():
    top_parser = argparse.ArgumentParser()
    subparsers = top_parser.add_subparsers(dest="subcommand")
    build_kanban_subparser(subparsers)

    args = top_parser.parse_args(["kanban", "create", "Build auth module", "-u"])
    assert getattr(args, "ultrawork", False) is True
