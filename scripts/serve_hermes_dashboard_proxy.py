"""Transparent Reverse Proxy for Hermes Web Dashboard on port 9191.

Listens on 0.0.0.0:9191 and proxies all traffic to Hermes Dashboard on 127.0.0.1:9119.
Rewrites the 'Host:' and 'Origin:' headers transparently on all HTTP requests (including keep-alive streams)
and forwards raw bidirectional bytes for WebSockets (terminal, TUI, and logs).
"""

import asyncio
import os
import re
import sys

LISTEN_HOST = os.environ.get("HERMES_PROXY_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("HERMES_PROXY_PORT", "9191"))
TARGET_HOST = "127.0.0.1"
TARGET_PORT = int(os.environ.get("HERMES_TARGET_PORT", "9119"))

HOST_RE = re.compile(rb"(\r?\nHost:\s*)([^\r\n]+)(\r?\n)", re.IGNORECASE)
ORIGIN_RE = re.compile(rb"(\r?\nOrigin:\s*)([^\r\n]+)(\r?\n)", re.IGNORECASE)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, is_client_to_upstream: bool = False):
    target_host_bytes = f"{TARGET_HOST}:{TARGET_PORT}".encode("utf-8")
    target_origin_bytes = f"http://{TARGET_HOST}:{TARGET_PORT}".encode("utf-8")
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            if is_client_to_upstream:
                if b"Host:" in data or b"host:" in data:
                    data = HOST_RE.sub(rb"\g<1>" + target_host_bytes + rb"\g<3>", data)
                if b"Origin:" in data or b"origin:" in data:
                    data = ORIGIN_RE.sub(rb"\g<1>" + target_origin_bytes + rb"\g<3>", data)
            writer.write(data)
            await writer.drain()
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def handle_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter):
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
    except Exception as exc:
        err_msg = f"HTTP/1.1 502 Bad Gateway\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nUpstream Hermes dashboard unreachable on {TARGET_HOST}:{TARGET_PORT}: {exc}\n".encode("utf-8")
        client_writer.write(err_msg)
        await client_writer.drain()
        client_writer.close()
        return

    await asyncio.gather(
        pipe(client_reader, upstream_writer, is_client_to_upstream=True),
        pipe(upstream_reader, client_writer, is_client_to_upstream=False),
        return_exceptions=True,
    )


async def main():
    server = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT)
    print(f"[*] Hermes Dashboard Proxy listening on {LISTEN_HOST}:{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
