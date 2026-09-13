"""Tests for 'hermes haos' CLI commands (Frente 1).

Covers:
- hermes haos status [--json]
- hermes haos federation ping <peer_id>
- hermes haos skills list
- hermes haos skills promote <skill_id>
"""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.haos_cmd import (
    _get_haos_status,
    build_haos_parser,
    cmd_haos_federation_ping,
    cmd_haos_skills_list,
    cmd_haos_skills_promote,
    cmd_haos_status,
)
from hermes.platform.skills.spec import SkillSpec


def test_build_haos_parser():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    haos_parser = build_haos_parser(subparsers)
    assert haos_parser is not None

    # Test argument parsing for status
    args = parser.parse_args(["haos", "status", "--json"])
    assert args.subcommand == "haos"
    assert args.haos_command == "status"
    assert args.json is True

    # Test argument parsing for federation ping
    args = parser.parse_args(["haos", "federation", "ping", "node-alpha"])
    assert args.subcommand == "haos"
    assert args.haos_command == "federation"
    assert args.federation_command == "ping"
    assert args.peer_id == "node-alpha"

    # Test argument parsing for skills list
    args = parser.parse_args(["haos", "skills", "list"])
    assert args.subcommand == "haos"
    assert args.haos_command == "skills"
    assert args.skills_command == "list"

    # Test argument parsing for skills promote
    args = parser.parse_args(["haos", "skills", "promote", "test-skill"])
    assert args.subcommand == "haos"
    assert args.haos_command == "skills"
    assert args.skills_command == "promote"
    assert args.skill_id == "test-skill"


def test_haos_status_json(capsys):
    args = argparse.Namespace(json=True)
    ret = cmd_haos_status(args)
    assert ret == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "memory" in data
    assert "scopes" in data["memory"]
    assert "procedural_skills" in data
    assert "capabilities" in data
    assert "model_routes" in data
    assert "federated_nodes" in data


def test_haos_status_human(capsys):
    args = argparse.Namespace(json=False)
    ret = cmd_haos_status(args)
    assert ret == 0

    captured = capsys.readouterr()
    assert "HAOS PLATFORM STATUS SUMMARY" in captured.out
    assert "Tríade Memory Scopes" in captured.out
    assert "Procedural Skills" in captured.out
    assert "Universal Capabilities" in captured.out


def test_haos_federation_ping_success(capsys):
    args = argparse.Namespace(peer_id="remote-node-1", secret="test-secret")
    ret = cmd_haos_federation_ping(args)
    assert ret == 0

    captured = capsys.readouterr()
    assert "Handshake successful!" in captured.out
    assert "remote-node-1" in captured.out


def test_haos_federation_ping_failure(capsys):
    args = argparse.Namespace(peer_id="bad-node", secret="test-secret")
    with patch("hermes.platform.federation.orchestrator.FederatedOrchestrator.complete_handshake", side_effect=RuntimeError("HMAC mismatch")):
        ret = cmd_haos_federation_ping(args)
        assert ret == 1

        captured = capsys.readouterr()
        assert "Error during federation handshake" in captured.out


def test_haos_skills_list_empty(capsys):
    args = argparse.Namespace()
    with patch("hermes.platform.skills.procedural_engine.SkillRegistry.list_skills", return_value=[]):
        ret = cmd_haos_skills_list(args)
        assert ret == 0

        captured = capsys.readouterr()
        assert "No procedural skills currently registered" in captured.out


def test_haos_skills_list_with_skills(capsys):
    spec = SkillSpec(
        name="crypto-audit",
        version="1.0.0",
        description="Audits smart contracts",
        status="active",
    )
    args = argparse.Namespace()
    with patch("hermes.platform.skills.procedural_engine.SkillRegistry.list_skills", return_value=[spec]):
        ret = cmd_haos_skills_list(args)
        assert ret == 0

        captured = capsys.readouterr()
        assert "crypto-audit" in captured.out
        assert "1.0.0" in captured.out
        assert "active" in captured.out


def test_haos_skills_promote_not_found(capsys):
    args = argparse.Namespace(skill_id="missing-skill", version=None)
    with patch("hermes.platform.skills.procedural_engine.SkillRegistry.get", return_value=None):
        ret = cmd_haos_skills_promote(args)
        assert ret == 1

        captured = capsys.readouterr()
        assert "not found in registry" in captured.out


def test_haos_skills_promote_success(capsys):
    spec = SkillSpec(
        name="test-promote",
        version="1.0.0",
        description="Candidate skill",
        status="candidate",
    )
    args = argparse.Namespace(skill_id="test-promote", version=None)
    with patch("hermes.platform.skills.procedural_engine.SkillRegistry.get", return_value=spec):
        with patch("hermes.platform.skills.procedural_engine.SkillLifecyclePipeline.run_full_pipeline", return_value=(True, "Pipeline succeeded: skill 'test-promote@1.0.0' is active")):
            ret = cmd_haos_skills_promote(args)
            assert ret == 0

            captured = capsys.readouterr()
            assert "Pipeline succeeded" in captured.out


def test_haos_skills_promote_failure(capsys):
    spec = SkillSpec(
        name="failing-skill",
        version="1.0.0",
        description="Flawed skill",
        status="candidate",
    )
    args = argparse.Namespace(skill_id="failing-skill", version=None)
    with patch("hermes.platform.skills.procedural_engine.SkillRegistry.get", return_value=spec):
        with patch("hermes.platform.skills.procedural_engine.SkillLifecyclePipeline.run_full_pipeline", return_value=(False, "Validation failed")):
            ret = cmd_haos_skills_promote(args)
            assert ret == 1

            captured = capsys.readouterr()
            assert "Failed to promote skill" in captured.out


def test_haos_doctor_cli(tmp_path, capsys):
    from hermes_cli.haos_cmd import cmd_haos_doctor
    (tmp_path / "config.yaml").write_text("dummy: test\n", encoding="utf-8")
    (tmp_path / ".env").write_text("A6_API_KEY=test\n", encoding="utf-8")
    (tmp_path / ".env").chmod(0o600)

    args = argparse.Namespace(json=False)
    with patch.dict("os.environ", {"HAOS_HOME": str(tmp_path)}):
        ret = cmd_haos_doctor(args)
        assert ret in (0, 1)
        out = capsys.readouterr().out
        assert "HAOS DOCTOR" in out
        assert "HAOS_HOME" in out


def test_haos_doctor_json(tmp_path, capsys):
    from hermes_cli.haos_cmd import cmd_haos_doctor
    (tmp_path / "config.yaml").write_text("dummy: test\n", encoding="utf-8")
    (tmp_path / ".env").write_text("A6_API_KEY=test\n", encoding="utf-8")
    (tmp_path / ".env").chmod(0o600)

    args = argparse.Namespace(json=True)
    with patch.dict("os.environ", {"HAOS_HOME": str(tmp_path)}):
        ret = cmd_haos_doctor(args)
        assert ret in (0, 1)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "overall_status" in data
        assert "checks" in data
        assert data["summary"]["total"] >= 5
