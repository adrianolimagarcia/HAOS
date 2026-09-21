# Requirement-to-test matrix

| Requirement | Contract | Current implementation evidence | Required invariant test |
|---|---|---|---|
| Lease TTL is >0 and ≤5m; expiry/revoke blocks reads | secret-broker | `MAX_LEASE_TTL_MS`; lease map | acquire at cap succeeds, cap+1/zero fail; expired and revoked reads fail |
| Keyring status is explicit and fail-closed | secret-broker | not yet exposed by broker API | locked/unavailable status blocks reads and reports readiness dependency |
| Secret values/tokens are not audited or logged | secret-broker | audit sink receives metadata only | audit recorder proves no value/token/capability bytes |
| Deadline is enforced before side effects | architecture, mcp-a2a | request context validates deadline in broker | expired MCP/A2A request performs no tool call |
| MCP reconnect is bounded and breaker opens/half-opens | mcp-a2a | not yet implemented in `haos-edge` MCP loop | deterministic fake transport verifies backoff deadline, no duplicate non-idempotent call, open→probe→close |
| File resolution is descriptor-relative and fails closed without hardened primitive | file-engine | Linux `openat2`; non-Linux canonicalization fallback | symlink/rename race cannot escape; unavailable primitive returns unsupported, never string fallback |
| Health required vs optional dependency semantics | health-agent | liveness independent; readiness bool supplied by caller | optional outage leaves live/ready policy intact; required outage fails readiness; timeout is degraded/failed |
| Executor cleanup on all terminal paths | executor | pidfd and tree-kill helpers exist | success/error/cancel/timeout/panic each reaps child, closes fd, removes temp state; repeated cleanup is safe |
| Global bounds and redacted errors | architecture + all contracts | bounded JSON/frame constants | oversized inputs fail before allocation; errors contain IDs/codes only, no secrets/raw paths |

The matrix is a planning contract: rows marked “not yet implemented” identify remaining runtime work rather than claiming coverage.
