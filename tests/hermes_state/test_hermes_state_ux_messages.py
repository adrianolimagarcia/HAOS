"""Plain-language contracts for hermes_state user-facing errors (CLI UX message campaign, cluster D)."""

import hermes_state
from hermes_state import SessionResumeTooLargeError, format_session_db_unavailable


def test_resume_too_large_names_export_and_config_commands():
    text = str(SessionResumeTooLargeError(4312, 4000))
    assert "4312" in text and "4000" in text
    assert "hermes sessions export" in text
    assert "hermes config set sessions.max_resume_messages 0" in text
    for jargon in ("lineage", "guard", "safe resume limit"):
        assert jargon not in text


def test_resume_too_large_keeps_structured_fields():
    exc = SessionResumeTooLargeError(20_001, 20_000, scope="in its tip segment")
    assert (exc.message_count, exc.limit) == (20_001, 20_000)
    assert isinstance(exc, ValueError)


def test_db_unavailable_points_to_doctor_without_sqlite_internals():
    hermes_state._set_last_init_error("OperationalError: database is locked")
    try:
        text = format_session_db_unavailable(details=True)
    finally:
        hermes_state._set_last_init_error(None)
    lead, *rest = text.splitlines()
    assert "session history" in lead
    assert "will not be saved" in lead.lower()
    for internal in ("sqlite.org", "WAL", "NFS/SMB/FUSE/ZFS"):
        assert internal not in lead
    assert rest and rest[0].startswith("Details: ") and "database is locked" in rest[0]


def test_db_unavailable_is_one_line_for_chat_surfaces_by_default():
    hermes_state._set_last_init_error("OperationalError: database is locked")
    try:
        text = format_session_db_unavailable(prefix="Cannot resume")
    finally:
        hermes_state._set_last_init_error(None)
    assert "\n" not in text
    assert "Details:" not in text
    assert text.startswith("Cannot resume:")


def test_db_unavailable_unknown_cause_on_network_drive_points_at_moving_not_doctor_fix():
    hermes_state._set_last_init_error("OperationalError: locking protocol")
    try:
        text = format_session_db_unavailable()
    finally:
        hermes_state._set_last_init_error(None)
    assert "network" in text
    assert "local disk" in text
    assert "hermes doctor --fix" not in text


def test_db_unavailable_without_cause_still_names_doctor():
    hermes_state._set_last_init_error(None)
    text = format_session_db_unavailable(details=True)
    assert "hermes doctor" in text
    assert "will not be saved" in text.lower()
    assert "Details:" not in text


def test_db_unavailable_commands_are_pinned_to_the_failing_profile(monkeypatch, tmp_path):
    """Both fallbacks that bypass the shared cause table (no cause; network-drive gloss) name the
    profile whose store failed, like the table's actions do."""
    from hermes_constants import profile_cli_selector

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes" / "profiles" / "research"))
    selector = profile_cli_selector()
    assert selector.strip()
    hermes_state._set_last_init_error(None)
    no_cause = format_session_db_unavailable()
    hermes_state._set_last_init_error("OperationalError: locking protocol")
    try:
        network = format_session_db_unavailable()
    finally:
        hermes_state._set_last_init_error(None)
    for text in (no_cause, network):
        assert f"`hermes {selector}doctor`" in text and "`hermes doctor`" not in text
        assert "{profile_arg}" not in text


def test_quarantine_guidance_is_branded_and_is_actually_built(tmp_path, monkeypatch):
    """The state.db recovery guidance must be assembled from the active product name.

    Two failures hide here and only one of them is visible as a crash. The message is
    built eagerly, so a product helper that is called but never imported raises
    NameError on the exact path whose job is to tell the user how to recover — the
    user loses the recovery steps precisely when they need them. And a guidance
    string that hardcodes a product name is wrong for the other brand, silently.

    Asserting the produced text (not the source) covers both: building it must
    succeed, and under HAOS it must name `haos`.
    """
    import hermes_state
    from hermes_constants import product_cli_name, product_command

    monkeypatch.setenv("HAOS_HOME", str(tmp_path / ".haos"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".haos"))
    hermes_state._set_last_init_error(None)

    db = tmp_path / "state.db"
    db.write_bytes(b"")  # 0-byte: the quarantine path builds the guidance

    sdb = hermes_state.SessionDB(db_path=db)
    try:
        guidance = hermes_state.get_last_init_error()
    finally:
        sdb.close()
        hermes_state._set_last_init_error(None)

    assert guidance, "quarantine must record the recovery guidance"
    assert product_cli_name() == "haos", "HAOS_HOME must select the haos brand"
    assert product_command("sessions") in guidance
    assert "hermes sessions" not in guidance
    assert f"`{product_cli_name()}` chat only" in guidance
