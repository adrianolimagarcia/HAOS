"""Reverse Engineering Router for HAOS Platform (Phase 4 Cherry-pick).

Taxonomy and routing heuristics for selecting the appropriate reverse engineering tier:
- TRIAGE: fast header/metadata/security/string extraction without disassembly.
- BACKBONE: instruction-level disassembly, assembly flow, control flow graphs, cross-references via radare2.
- DEEP: high-level decompilation to C, complex data structure recovery, full algorithm reconstruction via Ghidra.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Dict, List, Optional


class RETier(str, Enum):
    TRIAGE = "triage"
    BACKBONE = "backbone"
    DEEP = "deep"


class RERouter:
    """Decides the most economical and effective tier for a reverse engineering query."""

    TRIAGE_KEYWORDS = [
        "triage", "header", "metadata", "entropy", "packed", "protection",
        "nx", "pie", "relro", "canary", "sections", "segments", "strings",
        "imports", "exports", "file type", "architecture",
    ]

    BACKBONE_KEYWORDS = [
        "disassemble", "disassembly", "asm", "assembly", "instructions",
        "opcodes", "call graph", "xrefs", "cross references", "control flow",
        "functions list", "entrypoint", "radare2", "r2",
    ]

    DEEP_KEYWORDS = [
        "decompile", "decompilation", "pseudocode", "c code", "source code",
        "struct", "structure", "algorithm recovery", "high level logic",
        "ghidra", "type reconstruction", "ast",
    ]

    @classmethod
    def _matches_keyword(cls, text: str, keyword: str) -> bool:
        """Match keyword as a distinct word or phrase."""
        pattern = r"\b" + re.escape(keyword) + r"\b"
        return bool(re.search(pattern, text, re.IGNORECASE))

    @classmethod
    def select_tier(cls, task_description: str, metadata: Optional[Dict[str, Any]] = None) -> RETier:
        """Select reverse engineering tier based on task description and optional metadata."""
        desc = task_description.lower()
        if metadata is not None and not task_description:
            return RETier.TRIAGE

        # Check deep keywords first (highest capability request)
        if any(cls._matches_keyword(desc, kw) for kw in cls.DEEP_KEYWORDS):
            return RETier.DEEP

        # Check backbone keywords (assembly and disassembly)
        if any(cls._matches_keyword(desc, kw) for kw in cls.BACKBONE_KEYWORDS):
            return RETier.BACKBONE

        # Check triage keywords (preliminary inspection)
        if any(cls._matches_keyword(desc, kw) for kw in cls.TRIAGE_KEYWORDS):
            return RETier.TRIAGE

        # Default to triage for unknown tasks to conserve compute and context
        return RETier.TRIAGE

    @classmethod
    def route_to_tool(cls, task_description: str) -> Dict[str, Any]:
        """Return execution guidance including recommended tool and expected artifacts."""
        tier = cls.select_tier(task_description)
        if tier == RETier.TRIAGE:
            return {
                "tier": tier.value,
                "recommended_tool": "binary_analyzer",
                "component": "skills/reverse-engineering/binary-triage",
                "cost": "minimal",
                "daemon_required": False,
            }
        elif tier == RETier.BACKBONE:
            return {
                "tier": tier.value,
                "recommended_tool": "radare2_mcp",
                "component": "hermes/platform/re/r2_backbone.py",
                "cost": "medium",
                "daemon_required": False,
            }
        else:
            return {
                "tier": tier.value,
                "recommended_tool": "ghidra_mcp",
                "component": "hermes/platform/re/ghidra_mcp_client.py",
                "cost": "heavy",
                "daemon_required": True,
            }
