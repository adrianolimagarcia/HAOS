# Bounded operational hierarchy

## Purpose
Implement a bounded operational hierarchy that diagnoses supplied storage,
memory and system telemetry. It does not perform remediation, background host
probing, model invocation or deployment.

## Contract
HierarchicalDiagnostician.diagnose(telemetry) provides synchronous results and
run(telemetry, tasks=None, cancel_event=None) provides asynchronous task-DAG
execution using canonical TaskSpec dependencies. Worker inputs are copied,
readiness follows declared prerequisites, and output follows task order.
Invalid DAGs, failure, timeout and cancellation are explicit result statuses
rather than implicit success. Async workers must cooperate with cancellation.

## Side effects
The built-in workers inspect only supplied telemetry, and this module does not
write files, invoke subprocesses, mutate host settings or make network calls.
The diagnostician records its latest result on the instance. Callers own any
persistence or action execution; injected trusted worker functions are
responsible for their own side effects.

## API
`HierarchicalDiagnostician` provides synchronous `diagnose(telemetry)` for the
operational pipeline and asynchronous `run(telemetry, tasks=None,
cancel_event=None)` for async integrations. `last_result` is instance-local;
`HierarchyResult.to_dict()` supplies dashboard/audit receipts. Failed synchronous
runs raise `HierarchyExecutionError` carrying the result, rather than silently
planning from partial diagnoses. Async callers inspect `result.status`.

## Executable specialization
The supervisor creates canonical `TaskSpec` children for storage, memory and
system. Default workers actually analyze disjoint observation snapshots: WAL
size/drive temperature/USB BDI settings, memory usage, and load versus online
CPUs. Causes remain unknown with confidence zero; proposed actions are
investigation recommendations, not executed remediation. Scoped source errors
become evidence-backed unknown-telemetry findings.

Injected workers are trusted async `WorkerContext -> list[Diagnosis]` callables.
They receive only domain telemetry and declared dependency results. Input task
specs and snapshots are copied. Canonical `requires_tasks` gates readiness;
missing/cyclic/duplicate graphs and inconsistent typed edges fail admission.
Children cannot delegate through this interface. Team/mission runtimes are not
instantiated: their worktree/dispatcher/persistence lifecycle is unrelated to a
single observation cycle; their TaskSpec dependency contract is reused instead.

## Bounds and failure behavior
Admission has finite task, active-worker, per-worker timeout, overall timeout and
accepted-finding budgets. Failures block downstream tasks while independent
siblings may complete. External cancellation cancels and drains admitted workers;
parent coroutine cancellation does likewise. Result and diagnosis order follows
input task order regardless of completion timing. Exceptions, timeout, blocked,
cancelled and global finding-budget exhaustion are explicit receipts.

## Limits
Timeout/cancellation is cooperative asyncio cancellation, not a hard process kill.
A blocking or cancellation-suppressing injected worker can defeat elapsed-time
bounds; untrusted capabilities require an isolated executor elsewhere. There is
no LLM spawning, model-token budget, mutation executor, new core tool, persistent
mission creation or implicit worker trust/confidence scoring. Recommendations do
not establish cause or verify remediation. Default workers use supplied telemetry
and perform no additional host probing. The pipeline owns receipt persistence.

## Verification
`scripts/run_tests.sh tests/platform/agent_framework/test_hierarchy.py`: final
run observed 14 passed. Behavioral coverage includes simultaneous admission,
parallel ceiling, dependency input scoping/order, errors and sibling survival,
caller/event cancellation cleanup, worker/global timeouts, invalid DAG rejection,
finding/task budgets, evidence validation, and real ObserverAgent ->
HAOSOrchestrator -> hierarchy against synthetic proc/sys files and a sparse WAL in
a temporary profile. No production state, deploy, commits or ISO writes performed.
