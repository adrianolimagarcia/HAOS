# Phase A architecture contract

## Scope and ownership

Rust-native components are separate processes or libraries with explicit owners:

| Component | Owns | Must not own |
|---|---|---|
| Event bridge | ingress/egress event ordering, IDs, acknowledgements | secrets, filesystem policy |
| Secret broker | secret lookup, lease/redaction, audit | arbitrary network or file access |
| File engine | capability-scoped file operations and quotas | secret material, event routing |
| Health agent | liveness/readiness observations and bounded diagnostics | remediation or credentials |
| MCP/A2A adapter | protocol translation and capability negotiation | policy bypass or direct secret access |

Each component has one authoritative state owner. Cross-component state is exchanged only through the contracts in `contracts/`; no shared mutable globals.

## Trust boundaries and threat model

Inputs are hostile at every process boundary: malformed frames, replay, duplicate delivery, oversized payloads, path traversal, symlink races, confused-deputy requests, secret exfiltration, resource exhaustion, and downgrade/version confusion are in scope. Local IPC is not trusted merely because it is local. Authenticate peers where transport supports it; otherwise bind to a private endpoint with OS permissions and a per-instance nonce.

Threat assumptions: the host kernel and configured transport are trusted; a compromised component may forge its own outputs but cannot read another component's memory. Defenses are least privilege, explicit capability tokens, canonicalized paths, bounded parsing, constant-time secret comparisons where applicable, redacted logs, and fail-closed policy decisions. Availability attacks are mitigated with quotas and deadlines, not unbounded retries.

## Global limits

Unless a narrower contract says otherwise: frame 1 MiB, header 4 KiB, nesting depth 32, string 64 KiB, batch 128 items, in-flight requests 64 per peer, event replay window 10,000 events or 24 hours, and clock skew tolerance 30 seconds. Implementations may lower limits, never silently raise them.

## Errors and deadlines

Errors are structured (`code`, `message`, `retryable`, `details`, `request_id`); messages contain no secrets or raw paths. Stable codes include `INVALID_ARGUMENT`, `UNAUTHENTICATED`, `PERMISSION_DENIED`, `NOT_FOUND`, `CONFLICT`, `RESOURCE_EXHAUSTED`, `DEADLINE_EXCEEDED`, `UNAVAILABLE`, `INTERNAL`, and `PROTOCOL_ERROR`. Deadlines are absolute RFC3339 timestamps or monotonic durations carried in the envelope; receivers reject expired work before side effects. Retries are caller-owned, bounded, and only for explicitly retryable errors. Request IDs provide idempotency; duplicate non-idempotent requests return the original result or `CONFLICT`.

## Framing and versioning

Transport framing is length-prefixed big-endian `u32`, followed by UTF-8 JSON (or negotiated binary payload). A frame length of zero is invalid; parsers enforce the global limits before allocation. Every message carries `protocol`, `version`, `kind`, `request_id`, `sent_at`, optional `deadline`, and `body`. Unknown fields are ignored; unknown required versions/kinds fail with `PROTOCOL_ERROR`. Compatibility is additive within a major version; major changes require negotiation and a new contract revision.

## Observability and shutdown

Logs use request ID, peer ID, component, outcome, and latency; secrets and bearer tokens are always redacted. Readiness means dependencies and policy are usable; liveness means the process can make progress. Shutdown stops intake, rejects new work, drains within a deadline, then cancels and reports incomplete operations. Health output is bounded and must not expose secret values or unrestricted filesystem paths.
