"""Boundary contract: prevent an unanchored over-window compression dead end."""
import pytest
from agent.turn_context_compaction import _force_oversized_unanchored_preflight as force


@pytest.mark.parametrize("fraction,expected", [
    (0.70, False), (0.85, False), (0.95, False),
    (1.00, True), (1.20, True),
])
def test_context_boundary(fraction, expected):
    assert force(True, int(100_000 * fraction), 100_000, False) is expected


def test_anchored_or_post_compression_usage_latch_not_overridden():
    assert force(False, 120_000, 100_000, False) is False
    assert force(True, 120_000, 100_000, True) is False


def test_invalid_context_does_not_force():
    assert not force(True, 200_000, None, False)
    assert not force(True, 200_000, True, False)
    assert not force(True, 200_000, 0, False)
