#!/usr/bin/env bash
set -u
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
RECEIPT="${MCP_A2A_RECEIPT:-$ROOT/SDD/rust-native/harness/mcp-a2a-receipt.json}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
python3 "$ROOT/SDD/rust-native/harness/mcp_a2a_peer.py" --socket "$TMP/peer.sock" --ready "$TMP/ready" >"$TMP/peer.out" 2>"$TMP/peer.err" & peer=$!
cleanup(){ kill "$peer" 2>/dev/null || true; wait "$peer" 2>/dev/null || true; }
trap cleanup EXIT
for _ in $(seq 1 50); do [ -e "$TMP/ready" ] && break; sleep .02; done
python3 "$ROOT/SDD/rust-native/harness/mcp_a2a_probe.py" --socket "$TMP/peer.sock" --receipt "$RECEIPT"; rc=$?
cat "$TMP/peer.out"; cat "$TMP/peer.err" >&2
exit "$rc"
