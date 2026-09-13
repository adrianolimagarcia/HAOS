"""Minimal LSP JSON-RPC 2.0 framer over asyncio streams (stdlib only).

Wire format: ``Content-Length: <bytes>\\r\\n\\r\\n<utf-8 JSON body>`` where the
body is a JSON-RPC 2.0 request, response, or notification. This module mirrors
the framing semantics of the LSP spec and of ``agent/lsp/protocol.py`` (read
for reference, never imported: HAOS capabilities stay stdlib-only and never
couple to the upstream agent tree).
"""

import asyncio
import json
from typing import Any, Dict, Optional, Tuple

_MAX_HEADER_BYTES = 8192  # a well-behaved server fits in well under 200 bytes
_MAX_BODY_BYTES = 64 * 1024 * 1024


class LSPProtocolError(Exception):
    """The framing or envelope itself is broken (vs. a conformant JSON-RPC error response)."""


def encode_message(obj: dict) -> bytes:
    """Encode an envelope as compact UTF-8 JSON with an exact Content-Length header."""
    body = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


async def _read_headers(reader: asyncio.StreamReader) -> Optional[Dict[str, str]]:
    """Read the header block; ``None`` on clean EOF before any header started."""
    headers: Dict[str, str] = {}
    header_bytes = 0
    while True:
        try:
            line = await reader.readuntil(b"\r\n")
        except asyncio.IncompleteReadError as e:
            # EOF before any header started is a clean close; mid-block is bad framing.
            if not e.partial and not headers:
                return None
            raise LSPProtocolError(
                f"unexpected EOF while reading LSP headers (partial={e.partial!r})"
            ) from e
        header_bytes += len(line)
        if header_bytes > _MAX_HEADER_BYTES:
            raise LSPProtocolError("LSP header block exceeded 8 KiB without terminator")
        line = line[:-2]  # strip CRLF
        if not line:
            return headers  # blank line ends the header block
        try:
            key, _, value = line.decode("ascii").partition(":")
        except UnicodeDecodeError as e:
            raise LSPProtocolError(f"non-ASCII LSP header: {line!r}") from e
        if not key:
            raise LSPProtocolError(f"malformed LSP header line: {line!r}")
        headers[key.strip().lower()] = value.strip()


async def read_message(reader: asyncio.StreamReader) -> Optional[dict]:
    """Read one framed message.

    ``None`` on clean EOF between messages (typical shutdown);
    :class:`LSPProtocolError` on malformed framing.
    """
    headers = await _read_headers(reader)
    if headers is None:
        return None

    cl = headers.get("content-length")
    if cl is None:
        raise LSPProtocolError(f"LSP message missing Content-Length: {headers!r}")
    try:
        n = int(cl)
    except ValueError as e:
        raise LSPProtocolError(f"non-integer Content-Length: {cl!r}") from e
    if n < 0 or n > _MAX_BODY_BYTES:
        raise LSPProtocolError(f"unreasonable Content-Length: {n}")
    try:
        body = await reader.readexactly(n)
    except asyncio.IncompleteReadError as e:
        raise LSPProtocolError(
            f"truncated LSP body: expected {n} bytes, got {len(e.partial)}"
        ) from e
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as e:
        raise LSPProtocolError(f"invalid JSON in LSP body: {e}") from e
    except UnicodeDecodeError as e:
        raise LSPProtocolError(f"non-UTF-8 LSP body: {e}") from e


def make_request(req_id: int, method: str, params: Any) -> dict:
    """Build a JSON-RPC 2.0 request envelope."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        **({} if params is None else {"params": params}),
    }


def make_notification(method: str, params: Any) -> dict:
    """Build a JSON-RPC 2.0 notification envelope (no ``id``)."""
    return {
        "jsonrpc": "2.0",
        "method": method,
        **({} if params is None else {"params": params}),
    }


def classify_message(msg: dict) -> Tuple[str, Any]:
    """Return ``(kind, key)``: kind is request/response/notification/invalid;
    key is the id (request/response), the method (notification), or ``None``."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return "invalid", None
    if "id" in msg:
        if "method" in msg:
            return "request", msg["id"]
        if "result" in msg or "error" in msg:
            return "response", msg["id"]
        return "invalid", None
    if "method" in msg:
        return "notification", msg["method"]
    return "invalid", None


__all__ = [
    "LSPProtocolError",
    "encode_message",
    "read_message",
    "make_request",
    "make_notification",
    "classify_message",
]
