# Event-driven autonomy (opt-in)

The framework worker consumes bounded events and produces durable investigation
results and saved dry-run plans. It is **disabled by default** and does not change
`haos framework run` (which remains a dry-run preview).

## Configure the active profile

Use canonical `config.yaml` settings, not environment switches:

```sh
haos config set framework.autonomy.enabled true
haos config set framework.autonomy.max_jobs_per_hour 6
haos config set framework.autonomy.max_pending 128
haos config set framework.autonomy.investigation_timeout 90
haos config set framework.autonomy.max_output_tokens 2048
haos framework autonomy serve
```

`serve` stays in the foreground for supervision by an operator or an existing
supervisor. SIGINT/SIGTERM request graceful shutdown. It does not install a
service or daemonize. Concurrency is hard-limited to **one**, not configurable.

| Setting | Default | Valid range |
|---|---:|---|
| `enabled` | false | boolean |
| `auto_apply` | false | boolean |
| `poll_seconds` | 15 | 1–120 |
| `telemetry_seconds` | 60 | 15–3600 |
| `cooldown_seconds` | 300 | 30–86400 |
| `max_pending` | 128 | 1–128 |
| `max_jobs_per_hour` | 6 | 1–24 |
| `investigation_timeout` | 90 | 10–180 |
| `max_output_tokens` | 2048 | 256–4096 |
| `event_patterns` | `['*.failed', '*.error', '*.warning']` | 1–16 patterns, each 1–128 characters |

Unknown settings and invalid types/ranges are rejected rather than relaxing
limits. Pipeline/autonomy outcome events are excluded even with wildcard
patterns, preventing feedback loops. Host telemetry collection is read-only,
but investigation sends bounded event and operational evidence to the configured
model provider, which may be remote. No separate telemetry/analytics SaaS is
introduced. Secrets are not intentionally collected; keep secrets out of source
event payloads and operator summaries.

Investigation has runtime tool isolation: the model cannot use operational tools.
This is **not an OS sandbox** for the provider client process. Job, timeout and
output-token caps bound work, not a currency budget; provider billing and complete
cost accounting are not established by these limits.

## Inspect and control

```sh
haos framework autonomy status --json
haos framework autonomy pause
haos framework autonomy event --summary "Investigate the failed backup"
haos framework autonomy resume
```

Every leaf command accepts `--base-dir` (default: active profile home/agent)
and `--json`. Status inspects the queue and service heartbeat without creating
storage or starting a worker (the CLI entrypoint may still write its normal logs).
New event sources enroll at their current tail rather than replaying historical
failures. A missing or older-than-120-second heartbeat is
reported stale; a heartbeat alone is not a process-liveness guarantee.
Pause/resume persist queue admission-to-execution control; pause stops new
execution, not an already-applied action. Configuration is re-read by the worker
at execution boundaries, so disabling it prevents subsequent work. Resume does **not**
enable disabled configuration or start a service.

Operator events persist `operator.investigation` with source `cli`. A summary
must be nonblank and at most 2000 characters. It is untrusted description data,
not arbitrary shell commands. Identical summaries share cooldown identity;
distinct summaries remain distinct investigations. Admission rejection returns
a nonzero exit. Queue state, budgets, jobs and heartbeat are profile-local.

## Authorization is unchanged

AI output **never authorizes itself**. Default behavior is report/plan only.
`auto_apply: true` merely allows trying the existing gated execution path: exact
policy grants for each saved action/target/parameters and rollback are still
required, including explicit autonomous grants. Consent, a suggested action,
or a model-generated plan is not a policy grant. Do not add broad repair grants.

Host events are report-only: no general host repair, arbitrary command execution,
or privilege expansion is introduced. Review saved plans through the existing
`framework plan`, `grant`, `approve`, and `apply` operator workflow. Recovered
uncertain work must not blindly repeat a mutation; durable receipts and existing
execution gates remain authoritative.
