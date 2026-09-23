"""Translate OpenCode's existing OpenAI/SSE stream to the HAOS contract.

This adapter is intentionally response-only.  The OpenCode sidecar currently
exposes the OpenAI-compatible chat response stream, not a task-control API.
Consequently it derives observations only from frames actually received and
never creates server-side execution state.
"""

from __future__ import annotations

import json
from typing import Iterable, Iterator, Mapping

from hermes.platform.execution.execution_contract import (
    ExecutionError,
    ExecutionErrorCode,
    ExecutionEvent,
    ExecutionStatus,
)


class OpenCodeProtocolError(ValueError):
    """The upstream stream is not a valid OpenAI-compatible SSE response."""


class OpenCodeCapabilityUnsupported(NotImplementedError):
    """The verified OpenCode sidecar contract lacks a requested capability."""


class OpenCodeCancellationUnsupported(OpenCodeCapabilityUnsupported):
    """OpenCode's verified sidecar contract has no HTTP cancellation operation."""


def _unsupported(operation: str) -> None:
    raise OpenCodeCapabilityUnsupported(
        f"OpenCode sidecar does not expose a verified {operation} operation"
    )


def status(*_args: object, **_kwargs: object) -> None:
    """Reject status polling: the verified sidecar has no status endpoint."""
    _unsupported("status endpoint")


def events(*_args: object, **_kwargs: object) -> None:
    """Reject event subscription: events exist only within a chat SSE response."""
    _unsupported("events endpoint")


def restart(*_args: object, **_kwargs: object) -> None:
    """Reject restart: the adapter does not own the sidecar process lifecycle."""
    _unsupported("restart operation")


def resume(*_args: object, **_kwargs: object) -> None:
    """Reject resume: no durable execution/session API is verified."""
    _unsupported("resume operation")


def artifacts(*_args: object, **_kwargs: object) -> None:
    """Reject artifact retrieval: no artifact API is verified."""
    _unsupported("artifact operation")


def cancellation_supported() -> bool:
    """Return the capability advertised by this adapter.

    This is deliberately a constant: no cancellation endpoint was verified on
    the upstream sidecar, so disconnecting a client must not be presented as
    cancelling the upstream generation.
    """

    return False


def _event(execution_id: str, status: ExecutionStatus, timestamp: float, **kwargs: object) -> ExecutionEvent:
    return ExecutionEvent(execution_id, status, timestamp, **kwargs)


def iter_events(
    execution_id: str,
    sse_lines: Iterable[str],
    *,
    timestamp: float = 0.0,
) -> Iterator[ExecutionEvent]:
    """Yield contract observations from an OpenAI-compatible SSE stream.

    ``timestamp`` is supplied by the caller because the adapter has no clock
    or durable execution record.  One RUNNING event is emitted for the first
    valid data frame, terminal completion is emitted only for ``[DONE]``, and
    an upstream error frame becomes FAILED.  Malformed frames fail closed with
    :class:`OpenCodeProtocolError` rather than inventing a terminal state.
    """

    if not isinstance(execution_id, str) or not execution_id.strip():
        raise ValueError("execution_id must be a non-empty string")

    started = False
    saw_done = False
    for raw_line in sse_lines:
        if not isinstance(raw_line, str):
            raise OpenCodeProtocolError("SSE line must be a string")
        line = raw_line.rstrip("\r\n")
        if not line or line.startswith(":"):
            continue
        if saw_done:
            # The verified sidecar currently emits a trailing metadata frame
            # after [DONE].  A terminal SSE marker seals the response; ignore
            # every later field rather than turning post-terminal metadata into
            # duplicate/error lifecycle events.
            continue
        if not line.startswith("data:"):
            raise OpenCodeProtocolError("unsupported SSE field")
        payload = line[5:].lstrip()
        if payload == "[DONE]":
            saw_done = True
            if started:
                yield _event(execution_id, ExecutionStatus.COMPLETED, timestamp)
            continue
        try:
            frame = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OpenCodeProtocolError("invalid SSE JSON") from exc
        if not isinstance(frame, Mapping):
            raise OpenCodeProtocolError("SSE payload must be a JSON object")

        error = frame.get("error")
        if error is not None:
            message = error if isinstance(error, str) else error.get("message") if isinstance(error, Mapping) else None
            if not isinstance(message, str) or not message.strip():
                message = "OpenCode upstream error"
            yield _event(
                execution_id,
                ExecutionStatus.FAILED,
                timestamp,
                error=ExecutionError(ExecutionErrorCode.BACKEND_ERROR, message),
            )
            return

        if not started:
            started = True
            yield _event(
                execution_id,
                ExecutionStatus.RUNNING,
                timestamp,
                metadata={"source": "opencode_sse"},
            )

    # A closed response without [DONE] is not success.  Do not emit a made-up
    # terminal state; the caller can classify the transport close separately.
    if started and not saw_done:
        return


def cancel(*_args: object, **_kwargs: object) -> None:
    """Reject cancellation instead of implying that a disconnect cancels work."""

    raise OpenCodeCancellationUnsupported(
        "OpenCode sidecar exposes no verified HTTP cancellation operation"
    )


__all__ = [
    "OpenCodeCancellationUnsupported",
    "OpenCodeCapabilityUnsupported",
    "OpenCodeProtocolError",
    "artifacts",
    "cancel",
    "cancellation_supported",
    "events",
    "iter_events",
    "restart",
    "resume",
    "status",
]
