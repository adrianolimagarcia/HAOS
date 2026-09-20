# Health agent contract

Health probes are bounded, read-only observations with `component`, `check`, `deadline`, and correlation ID. Results are `status` (`live`, `ready`, `degraded`, `failed`), `observed_at`, latency, and a short stable reason code. Probe execution has a 2-second default deadline, 32 checks per request, and no recursive dependency probing. Liveness must not depend on optional services; readiness may. The agent does not restart processes or mutate configuration. Diagnostic details are redacted and capped at 4 KiB.
