"""Unit/label names for the gateway process — one source for the HAOS rename.

The fork ships ``haos-gateway``; upstream and pre-rename installs use
``hermes-gateway``. Both must be *recognized* everywhere a unit name is matched —
fleet discovery, restart-by-kind, the lifecycle guard, the updater's plan
reconciliation — or a renamed install silently stops being restarted by
``hermes update``. That failure is why this module exists instead of a literal in
each caller.

Generation lives in :func:`hermes_cli.gateway.get_service_name` (which appends the
profile suffix); matching lives here.
"""

GATEWAY_UNIT_BASE = "haos-gateway"
LEGACY_GATEWAY_UNIT_BASES = ("hermes-gateway",)
GATEWAY_UNIT_BASES = (GATEWAY_UNIT_BASE, *LEGACY_GATEWAY_UNIT_BASES)


def _strip_service_suffix(name: str) -> str:
    name = (name or "").strip()
    return name[: -len(".service")] if name.endswith(".service") else name


def is_gateway_unit(unit_name: str) -> bool:
    """True for a gateway unit and its per-profile variants, for either base.

    Prefix-anchored on ``-`` so a near-prefix like ``hermes-gatewayd`` never
    matches — the substring-matching bug class this repo bans.
    """
    name = _strip_service_suffix(unit_name)
    return any(name == base or name.startswith(f"{base}-") for base in GATEWAY_UNIT_BASES)


def gateway_unit_globs() -> list:
    """``systemctl`` glob patterns covering every gateway unit, both bases."""
    return [f"{base}*" for base in GATEWAY_UNIT_BASES]
