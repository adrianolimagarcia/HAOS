"""Reverse Engineering capabilities for HAOS Platform.
Layered architecture: triage (Phase 1), radare2 backbone (Phase 2), Ghidra deep (Phase 3).
"""

from hermes.platform.re.r2_backbone import Radare2MCPClient

__all__ = ["Radare2MCPClient"]
