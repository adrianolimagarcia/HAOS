"""Regression coverage for the stale Antigravity custom-provider slug.

The configured legacy entry is named ``Antigravity (Gemini)``.  Its canonical
legacy-list identity is therefore ``custom:antigravity-(gemini)``; the
historical ``custom:antigravity-gemini`` spelling is not an implicit alias.
Unknown names must remain fail-closed rather than being routed to a guessed
endpoint or a fictitious provider.
"""

from __future__ import annotations

import yaml
import pytest

from hermes_cli.auth import AuthError
from hermes_cli.config import get_compatible_custom_providers, load_config
from hermes_cli.providers import resolve_provider_full
from hermes_cli.runtime_provider import resolve_runtime_provider


ENTRY_NAME = "Antigravity (Gemini)"
ENTRY_URL = "http://127.0.0.1:8790/v1"
VALID_SLUG = "custom:antigravity-(gemini)"
INVALID_SLUG = "custom:antigravity-gemini"


def _isolated_antigravity_config(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {"default": "gemini-3.7-flash-low", "provider": INVALID_SLUG},
                "custom_providers": [
                    {
                        "name": ENTRY_NAME,
                        "base_url": ENTRY_URL,
                        "api_key": "test-antigravity-key",
                        "model": "gemini-3.7-flash-low",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    return load_config()


def test_historical_antigravity_slug_is_unknown_and_fails_closed(tmp_path, monkeypatch):
    """A punctuation-dropped slug must not silently select the configured endpoint."""
    cfg = _isolated_antigravity_config(tmp_path, monkeypatch)
    entries = get_compatible_custom_providers(cfg)

    assert resolve_provider_full(INVALID_SLUG, cfg.get("providers"), entries) is None
    with pytest.raises(AuthError, match="Unknown provider 'custom:antigravity-gemini'"):
        resolve_runtime_provider(requested=INVALID_SLUG, target_model="gemini-3.7-flash-low")


def test_configured_antigravity_name_resolves_only_with_its_exact_slug(tmp_path, monkeypatch):
    """The configured entry remains usable through its actual canonical identity."""
    cfg = _isolated_antigravity_config(tmp_path, monkeypatch)
    entries = get_compatible_custom_providers(cfg)

    provider = resolve_provider_full(VALID_SLUG, cfg.get("providers"), entries)
    assert provider is not None
    assert provider.id == VALID_SLUG
    assert provider.base_url == ENTRY_URL

    runtime = resolve_runtime_provider(requested=VALID_SLUG, target_model="gemini-3.7-flash-low")
    assert runtime["provider"] == "custom"
    assert runtime["base_url"] == ENTRY_URL
