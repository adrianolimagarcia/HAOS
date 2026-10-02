"""Builtin memory prefetch must obey the same spill bound as external providers.

`_prefetch_provider` stamped the builtin provider's raw registry/notes mirror into
the user turn's `api_content` (compose_user_api_content → _stamp_api_content_sidecar),
where it is replayed on EVERY later turn — the exact cascade the external path's
`spill_if_oversized` bound exists to prevent. The external branch's comment says so
explicitly; the builtin branch was exempt. These tests pin the parity: an oversized
builtin prefetch spills to `<HERMES_HOME>/hook_outputs/<session>/` and returns a
preview pointer; a small one passes through byte-identical.
"""

from __future__ import annotations

from pathlib import Path

from agent.memory_manager import MemoryManager


class _StubBuiltin:
    """Duck-typed builtin provider (avoids the abstract-interface boilerplate)."""

    name = "builtin"
    plugin_id = None

    def __init__(self, payload):
        self._payload = payload

    def prefetch(self, query, *, session_id=""):
        return self._payload


def _manager(payload) -> MemoryManager:
    mm = MemoryManager()
    mm._providers.append(_StubBuiltin(payload))
    return mm


def test_oversized_builtin_prefetch_spills_and_returns_pointer():
    payload = "<memory-context>\n" + ("M" * 40_000) + "\n</memory-context>"
    out = _manager(payload).prefetch_all("recall please", session_id="sess_p1a")

    # Well under the 10,000-char default: header + 500 head + 500 tail only.
    assert len(out) < 5_000
    assert "truncated" in out and "40,0" in out  # 40,0xx chars reported
    assert "memory prefetch" in out  # source label names the builtin path
    assert "saved to" in out


def test_oversized_builtin_prefetch_writes_spill_file(tmp_path):
    from hermes_constants import get_hermes_home

    payload = "<memory-context>\n" + ("M" * 40_000) + "\n</memory-context>"
    _manager(payload).prefetch_all("q", session_id="sess_p1a_file")

    spill_dir = Path(get_hermes_home()) / "hook_outputs" / "sess_p1a_file"
    files = list(spill_dir.glob("*.txt"))
    assert files, f"no spill file under {spill_dir}"
    assert files[0].read_text().startswith("<memory-context>")


def test_small_builtin_prefetch_passes_through_unchanged():
    payload = "short registry mirror"
    assert _manager(payload).prefetch_all("q", session_id="s") == payload


def test_empty_and_none_builtin_prefetch_do_not_crash():
    assert _manager("").prefetch_all("q", session_id="s") == ""
    assert _manager(None).prefetch_all("q", session_id="s") == ""
