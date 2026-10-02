# HAOS operational framework: operator guide

## Scope and default behavior

`hermes/platform/agent_framework` implements a bounded operational cycle separate
from existing mission/task execution, RAGGraph events and cron scheduling.
HAOSOrchestrator composes telemetry observation, supervised domain diagnosis,
planning, policy-gated execution and verification. **`run` and `plan` default to
dry-run.** Importing the package activates neither autonomy nor a scheduler.

```sh
haos framework status --json
haos framework observe --json
haos framework run --json
haos framework run --dry-run --json
haos framework plan --report --json
haos -p myprofile framework status --json
```

All commands accept `--base-dir DIR` and `--json`. The canonical installed entry
is `hermes_cli.main`; installer/distro wrappers forward `framework` there. Legacy
checkout `bin/haos framework ...` delegates to the same parser.

`--base-dir` is the framework root, not the profile home. Otherwise storage resolves
at invocation time to the active `get_hermes_home()/agent`. For development use a
temporary `HERMES_HOME`, unset any overriding `HAOS_HOME`, and never use live stores.

- **status:** latest persisted state; absent state returns `initialized: false`
  without creating framework storage.
- **observe:** bounded read-only Linux `/proc` and `/sys` telemetry, plus metadata
  for the profile's `state.db-wal`. With `--base-dir`, the WAL path is its parent's
  `state.db-wal`. No SQLite checkpoint or service-health probe is performed.
  Missing/invalid sources remain explicitly unknown, not healthy.
- **run:** one dry-run diagnosis/plan/verify cycle.
- **plan:** saves a dry-run plan. `--report` adds the sole built-in reversible
  mutation: `workspace_config_update` of the fixed artifact
  `<base-dir>/artifacts/operational-report.json`. It never modifies `config.yaml`.

Dry-run writes local state, plans, operational memory and audit events; it does
**not** mean zero filesystem writes. Simulated steps never establish remediation
success. Inspect verification, outcome and receipt rather than relying on exit 0.

## Explicit operator authorization and apply

Review the saved plan's ID, step ID, action, target and exact parameters first.
There are two separate assisted authorization gates: a durable **exact policy
grant**, and an **expiring approval bound to the saved plan intent**.

```sh
# Substitute IDs returned by plan --report, after reviewing its JSON.
haos framework grant PLAN_ID --step STEP_ID --json
haos framework approve PLAN_ID --step STEP_ID --ttl 300 --json
haos framework apply PLAN_ID --approval APPROVAL_ID --json
```

`grant` persists the exact action/target/parameters with rollback permission in
`agent-policy.yaml`. It cannot install a new handler or authorize arbitrary
filesystem targets. `approve` issues a stored approval with TTL at most 3600
seconds. IDs alone are not authorization; altering plan intent invalidates the
approval. Assisted apply is the default and requires both gates.

Autonomous execution is **off by default** (no exact autonomous grants). It is
reachable only by explicit operator opt-in for the reviewed intent:

```sh
haos framework grant PLAN_ID --step STEP_ID --autonomous --json
haos framework apply PLAN_ID --mode autonomous --json
```

This grants only the exact saved report-artifact action, not broad host autonomy.
The CLI accepts saved plan IDs, not raw plan files, shell strings or target paths.
New findings/parameters require a new reviewed exact grant. There is no enabled
privileged service repair, package installation, kernel tuning, arbitrary command
runner, firewall/disk operation or automatic execution daemon.

## Verification, rollback and interrupted execution

Execution captures pre-state before writing, verifies artifact postconditions,
and records durable receipts under `receipts/`. Failed/cancelled attempted actions
use their permitted rollback handler to restore captured pre-state; inspect the
receipt for rollback success or failure. This is **artifact rollback**, not OS
rollback or a transaction over all framework audit/state writes.

A saved plan is one-use: even a completed receipt prevents replay. An interrupted
or partial receipt blocks further mutation pending operator investigation;
there is no automatic crash replay or recovery command. Preserve receipts and
artifacts, investigate actual state, and do not delete evidence to force a retry.

## Hierarchy and dashboard

The default hierarchy is a supervisor over trusted in-process async storage,
memory and system diagnosis workers using canonical TaskSpec partitioning.
Concurrency, task count, findings and cooperative deadlines are bounded. Workers
receive scoped snapshots, cannot spawn child tasks or execute remediation;
failures block dependencies without discarding independent siblings. These are
not autonomous LLM subprocesses or fleet delegation. Blocking/untrusted workers
require separate isolation.

The authenticated profile-scoped `/framework` dashboard provides status,
telemetry, plans, actual rollback/action receipts, hierarchy and dry-run previews.
Explicit confirmations can issue exact grants and expiring approvals and apply
saved report-artifact plans. Cookie-auth mutations require a same-origin browser
request. See [FRAMEWORK_DASHBOARD_API.md](FRAMEWORK_DASHBOARD_API.md).
Scheduler-driven operational mutation and unrestricted fleet orchestration are
not implemented. Bounded report autonomy is not autonomous OS management.

## Findings and validation boundaries

No third-party source absorption or license-validation claim is made. Checkout
implementation and tests are distinct from VM acceptance and production deploy;
this guide makes no deployment claim. Follow [DEV_WORKFLOW_VM.md](DEV_WORKFLOW_VM.md)
for separately authorized appliance work; no ISO is required for CLI validation.

Foundation storage rejects malformed state and unsafe checkpoint manifests,
uses flushed atomic replacements, and rejects detected symlinks. Checkpoint
restoration is atomic per file, not a multi-file transaction. Audit append errors
can leave a partial trailing JSONL line; they prevent callback/history admission.
These checks do not promise protection against every hostile filesystem race.
Audit/plan retention is not automatic; cycle budgets are per orchestrator instance.

`--json` emits structured output. Storage, validation and replay errors return 1;
parser errors return 2. Successful command completion can still contain denied or
unverified actions—check the returned verification and receipt.

```sh
scripts/run_tests.sh tests/platform/agent_framework/ tests/hermes_cli/test_framework_cli.py
```

CLI tests use real imports, temporary homes, A→B→A profile isolation, canonical
and legacy entrypoints, exact grant/approval/apply behavior and replay denial.
