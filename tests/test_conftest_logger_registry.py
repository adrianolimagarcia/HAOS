"""The conftest logger-registry guard must make pytest's registry walk race-free.

``logging.Logger.manager.loggerDict`` is process-global: any thread that imports
a module declaring a logger, or calls ``logging.getLogger`` with a name that does
not exist yet, inserts into it. pytest's logging plugin walks it UNSNAPSHOTTED at
the start of every setup/call/teardown phase::

    _pytest/logging.py, catching_logs.__enter__:
        for logger in root_logger.manager.loggerDict.values():

so a background thread registering a logger mid-walk made the MAIN thread raise
``RuntimeError: dictionary changed size during iteration``. Landing in the
teardown phase, that also skipped ``SetupState.teardown_exact``: the setup stack
stayed dirty and the NEXT test died with ``AssertionError: previous item was not
torn down properly``.

``tests/conftest.py`` rebinds the registry to a snapshot-iterating mapping.
These tests pin the contract that makes the walk safe, and that the rebind keeps
``logging`` itself working.
"""

import logging
import uuid

import pytest

from tests.conftest import (
    _SnapshotIteratingDict,
    _install_snapshot_iterating_logger_registry,
)


def _fresh_logger_name() -> str:
    """A name no other test can have registered in this process."""
    return "hermes.tests.logger_registry_probe.%s" % uuid.uuid4().hex


def _registry():
    return logging.Logger.manager.loggerDict


@pytest.mark.parametrize("accessor", ["values", "keys", "items", "iter"])
def test_registry_walk_survives_a_registration_mid_walk(accessor):
    """A walk already in flight must not be invalidated by a concurrent insert."""
    registry = _registry()
    logging.getLogger(_fresh_logger_name())  # anchor: the walk is never empty
    source = registry if accessor == "iter" else getattr(registry, accessor)()
    walk = iter(source)
    next(walk)  # the walk has started, exactly like pytest's loop

    logging.getLogger(_fresh_logger_name())  # what a background thread does

    list(walk)  # a live view raises RuntimeError: dictionary changed size


def test_registry_still_registers_and_wires_loggers():
    """The rebound mapping must stay a working logging registry."""
    name = _fresh_logger_name()
    logger = logging.getLogger(name)
    assert logging.getLogger(name) is logger  # memoized
    assert _registry()[name] is logger  # reachable by name
    assert logger.parent is not None  # hierarchy fixup still ran
    assert logger.propagate is True  # default wiring intact


def test_install_is_idempotent_and_preserves_registered_loggers():
    """A second install must not churn the registry or drop what it holds."""
    name = _fresh_logger_name()
    logger = logging.getLogger(name)
    _install_snapshot_iterating_logger_registry()
    assert isinstance(_registry(), _SnapshotIteratingDict)
    assert _registry()[name] is logger
