"""Deterministic Local Guardrails (Zero-Cloud, First-Principles AI Safety).

Inspired by 'AI Engineering from Scratch':
- Detects prompt injection and jailbreak attempts using compiled regex automata.
- Scans and redacts PII and secrets (API keys, private keys, JWTs) without calling auxiliary LLMs.
- Blocks catastrophic destructive shell commands before execution.
100% offline, stdlib-only, ultra-low latency (< 1ms).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# 1. Prompt Injection & Jailbreak Automata
_INJECTION_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("system_override", re.compile(r"\b(ignore|disregard|forget|override)\s+(all\s+)?(previous|prior|above|system)\s+(instructions|directives|prompts|rules)\b", re.IGNORECASE)),
    ("jailbreak_persona", re.compile(r"\b(you are now|pretend you are|act as)\s+(unfiltered|unrestricted|DAN|developer mode|root|system)\b", re.IGNORECASE)),
    ("special_token_injection", re.compile(r"(<\|im_start\|>|<\|im_end\|>|<<SYS>>|<</SYS>>|\[INST\]|\[/INST\]|<\|system\|>)", re.IGNORECASE)),
    ("prompt_leak_attempt", re.compile(r"\b(repeat|reveal|output|display|show|print)\s+(your\s+)?(initial|system|original|secret)\s+(prompt|instructions|directive)\b", re.IGNORECASE)),
    ("instruction_boundary_smuggling", re.compile(r"---+\s*(BEGIN|START)\s+(SYSTEM|ADMIN|INTERNAL)\s+INSTRUCTION\s*---+", re.IGNORECASE)),
]

# 2. Secret & Token Leak Patterns
_SECRET_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("anthropic_api_key", re.compile(r"\bsk-ant-[a-zA-Z0-9_-]{20,80}\b")),
    ("openai_api_key", re.compile(r"\bsk-(?!ant-)[a-zA-Z0-9_-]{20,64}\b")),
    ("github_token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{10,72}\b")),
    ("jwt_token", re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b")),
    ("private_key", re.compile(r"-----BEGIN (RSA|EC|OPENSSH|DSA|PGP)? ?PRIVATE KEY-----[\s\S]*?-----END \1 ?PRIVATE KEY-----")),
]

# 3. Catastrophic Shell Execution Patterns
_CATASTROPHIC_COMMANDS: List[Tuple[str, re.Pattern]] = [
    ("root_deletion", re.compile(r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*\s+)+(/($|\s|\*)|--no-preserve-root)")),
    ("fork_bomb", re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:")),
    ("raw_disk_format", re.compile(r"\bmkfs\.[a-zA-Z0-9]+\s+/dev/")),
    ("raw_dd_wipe", re.compile(r"\bdd\s+if=[^\s]+\s+of=/dev/(sd[a-z]|nvme[0-9]n[0-9])\b")),
]


def detect_prompt_injection(text: str) -> Tuple[bool, List[str]]:
    """Detect prompt injection and jailbreak smuggling attempts deterministically."""
    if not text or not text.strip():
        return False, []

    matched_reasons: List[str] = []
    for label, pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            matched_reasons.append(f"injection_detected:{label}")

    return bool(matched_reasons), matched_reasons


def detect_secrets(text: str) -> Tuple[bool, List[str]]:
    """Scan text for leaked secrets and private API credentials."""
    if not text or not text.strip():
        return False, []

    matched_reasons: List[str] = []
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            matched_reasons.append(f"secret_detected:{label}")

    return bool(matched_reasons), matched_reasons


def redact_secrets(text: str) -> str:
    """Deterministically redact leaked secrets, replacing them with typed placeholders."""
    if not text or not text.strip():
        return text

    sanitized = text
    for label, pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(f"[REDACTED:{label.upper()}]", sanitized)

    return sanitized


def validate_shell_safety(command: str) -> Tuple[bool, Optional[str]]:
    """Check command against catastrophic system destruction signatures.

    Returns (is_safe, error_reason).
    """
    if not command or not command.strip():
        return True, None

    for label, pattern in _CATASTROPHIC_COMMANDS:
        if pattern.search(command):
            return False, f"catastrophic_command_blocked:{label}"

    return True, None
