# Secret broker contract

Requests contain `request_id`, capability token, secret name, purpose, and deadline. The broker returns an opaque lease handle plus expiry; secret bytes are never written to logs, events, or ordinary responses. Callers must explicitly release leases; expiry revokes them. Names are allowlisted and normalized, and access is audited with actor, purpose, outcome, and request ID (never value). Denials fail closed. Maximum lease lifetime is 5 minutes; maximum returned value is 64 KiB. No wildcard lookup, export, or pass-through to another component.
