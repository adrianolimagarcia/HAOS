"""Reverse Engineering capabilities for HAOS Platform.
Layered architecture: triage (Phase 1), radare2 backbone (Phase 2), Ghidra deep (Phase 3),
and Permission Broker (Phase 5).
"""

from hermes.platform.re.r2_backbone import Radare2MCPClient
from hermes.platform.re.broker import (
    REPermissionBroker,
    DecisionLogEntry,
    VALID_RE_GRANTS,
    MUTATING_GRANTS,
)

__all__ = [
    "Radare2MCPClient",
    "REPermissionBroker",
    "DecisionLogEntry",
    "VALID_RE_GRANTS",
    "MUTATING_GRANTS",
]
