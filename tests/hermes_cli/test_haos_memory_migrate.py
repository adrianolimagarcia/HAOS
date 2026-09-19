"""`haos memory migrate` — the entry point the MemoryMigrator never had.

The migrator shipped with a plan/backfill API and no caller outside its own tests, which is why
the canonical journal on the appliance was empty while the vault held 29 notes. The contract that
matters here is the one the command exists to protect: **without `--apply` nothing is written**.
A dry-run that writes is worse than no dry-run, because it is the thing an operator runs first to
decide whether to run it for real.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from hermes.platform.context.memory.canonical_store import CanonicalMemoryStore


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "curadoria").mkdir(parents=True)
    (vault / "curadoria" / "ADR-001-journal.md").write_text(
        "# ADR-001\n\nDECISION: the canonical journal is the only source of truth.\n", encoding="utf-8"
    )
    (vault / "nota.md").write_text("Retention window is 30 days.\n", encoding="utf-8")
    return vault


def _args(vault: Path, **overrides: object) -> argparse.Namespace:
    base = {"vault": str(vault), "scope": "project", "apply": False, "json": True}
    base.update(overrides)
    return argparse.Namespace(**base)


def _fabric_db() -> Path:
    from hermes_constants import get_hermes_home

    return Path(get_hermes_home()) / "memory" / "fabric.db"


def _counters() -> dict:
    db = _fabric_db()
    if not db.exists():
        return {}
    store = CanonicalMemoryStore(db)
    try:
        return store.operational_counters()
    finally:
        store.close()


def test_dry_run_plans_without_writing(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from hermes_cli.haos_cmd import cmd_haos_memory_migrate

    vault = _vault(tmp_path)
    assert cmd_haos_memory_migrate(_args(vault)) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["dry_run"] is True
    assert summary["notes_seen"] == 2
    assert summary["decisions"] == 1, "the ADR is a decision, the other note a fact"
    assert summary["imported"] == 0

    counters = _counters()
    assert counters.get("records_active", 0) == 0, "a dry-run must leave the journal empty"
    assert counters.get("outbox_events", 0) == 0, "and must not queue projections either"


def test_apply_imports_and_is_idempotent(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from hermes_cli.haos_cmd import cmd_haos_memory_migrate

    vault = _vault(tmp_path)
    assert cmd_haos_memory_migrate(_args(vault, apply=True)) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["imported"] == 2
    assert _counters()["records_active"] == 2

    # Re-running must not double the journal: the migrator keys on the legacy record id.
    assert cmd_haos_memory_migrate(_args(vault, apply=True)) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["imported"] == 0
    assert second["skipped_existing"] == 2
    assert _counters()["records_active"] == 2


def test_a_missing_vault_is_refused_without_touching_the_journal(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from hermes_cli.haos_cmd import cmd_haos_memory_migrate

    assert cmd_haos_memory_migrate(_args(tmp_path / "nope", apply=True)) == 2
    assert "não encontrado" in capsys.readouterr().out
    assert _counters().get("records_active", 0) == 0
