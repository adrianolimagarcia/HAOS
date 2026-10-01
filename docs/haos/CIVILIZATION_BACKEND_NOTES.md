# Civilization backend findings

## Observed runtime boundary

`civ_agents.json` and `civ_missions.json` hold declarative configuration and the UI mission record. Since the runtime integration below, start/pause/resume additionally synchronize canonical control state in `MissionStore` and drive `MissionSupervisor`, which launches per-mission loops that execute workflow nodes as Kanban cards with cooperative cancellation. Lifecycle disables future mission create/start/resume requests, including workflow-only assignments. It does not terminate already-running external processes; cancellation is cooperative via per-task `cancel_event`.

Agent status accepts only `active` or `disabled`. PUT IDs are immutable. Lifecycle takes strict `{enabled: boolean}` and returns the enriched agent. Unknown agents return 404; disabled mission references return 409; unknown mission references return 400. Start/simulate accept DRAFT/READY only; pause accepts RUNNING; resume accepts PAUSED.

## Approvals

Inspected `hermes/` event producers and `hermes_cli/web_routers/civilization.py`: no producer currently emits EventStore approval requests. Constitution/debate gates are not request records and are not fabricated into pending approvals.

The explicit supported producer contract is `civ.council.approval_requested` with nonempty string `approval_id`, optional string `mission_id`, `agent_id`, `gate`, `risk_class`, `description`; request time is the Event timestamp. GET `/api/civilization/approvals?mission_id=...` returns pending records only, with quality/limitations. Unknown decision requests return 404, already decided return 409. POST writes `civ.council.approval_decided` synchronously to the existing profile EventStore and propagates persistence failures rather than claiming success. A deterministic event_id uses EventStore's PK and transactional append to guarantee at most one successful router decision across concurrent connections/processes. Existing decisions of any event ID also suppress requests. Independent external writers must use this endpoint or identical event-ID contract for the concurrency guarantee.

EventStore is an observability stream, not a runtime/business ledger. For canonical runtime gates the decision path updates `approval_gates`, appends the audit event, calls `MissionSupervisor.wake()` and reports `runtime_resumed: true` when approved; for the EventStore projection fallback (no canonical gate row) the decision is audit-only and reports `runtime_resumed: false`.

## Analytics

`hermes/platform/execution/team_runtime.py` emits `team.formed` with mission_id and `mission.completed` with mission_id/status/total_tasks. Its intervening events are `subgoal.delegated`, `worker.acquired`, `task.completed`; no mission-linked token usage, retry counts or failure measurements are emitted. Kernel tracer token estimates/defaults and council token counts cannot be attributed to these mission IDs safely.

GET `/api/civilization/missions/{id}/analytics` returns `{mission_id, duration_seconds, tokens, failures, retries, event_count, quality, limitations}`. Duration is measured only from canonical team.formed→mission.completed timestamps. Missing endpoints or backwards timestamps yield null. Tokens/failures/retries are null, not fabricated zeros. Quality is `partial` with duration, otherwise `unmeasured`. JSON timelines, example timestamps and simulations never count as runtime metrics. Runtime-only IDs with canonical observations are accepted; wholly unknown IDs return404.

## Replay compatibility

Events retain legacy time/type/agent_id/description/status. Canonical store events add seq and allowlisted node_id/mission_status when explicitly recorded; node-specific recorded status is preserved. Raw payloads are never returned. Legacy JSON/store transition duplicates are matched by type and enriched; without correlation IDs this is best-effort and may conflate same-type legacy events. The API retains chronological timestamp ordering (seq breaks timestamp ties), not strict seq ordering across backdated entries. Missing historical state is not reconstructed from present-day nodes.

## Runtime integration status (current delivery)

`hermes/platform/execution/mission_store.py` provides additive, profile-owned SQLite metadata for mission control, task mappings, approval gates and measured usage records. It uses transactional writes/CAS and does not replace canonical `task_runs`. The Civilization create/start/pause/resume paths synchronize control metadata and return its source/state marker.

`hermes/platform/execution/mission_runtime.py` provides a profile-bound supervisor (`MissionSupervisor`) that compiles workflow nodes into canonical Kanban cards, admits work only while desired state is running, executes via `LaneWorker` (HermesCliLaneWorker in production, DeterministicLaneWorker under simulation or tests) with cooperative `cancel_event` propagation, and handles task completion/failure through `KanbanAdapter`.

Approval gates:
- Explicit approval nodes (`requires_approval: True`) register durable approval gates in `approval_gates`.
- Pending gates are returned via `/api/civilization/approvals` with source `canonical_runtime`.
- Operator decisions via `/api/civilization/approvals/{id}/decision` update the gate transactionally, append an audit event to EventStore, wake the `MissionSupervisor`, and report `runtime_resumed: true` for approved canonical gates.

Measured Analytics:
- `/api/civilization/missions/{id}/analytics` returns measured totals (`tasks_total`, `tasks_completed`, `tasks_failed`, `total_retries`, `total_tokens`, `duration_seconds`) when runtime execution data exists in `MissionStore`.
- For unexecuted/declarative-only missions, `tokens`, `failures`, and `retries` remain `None`, with limitations documented and quality marked `unmeasured`.

Lifespan and Cleanup:
- `_shutdown_supervisors` is integrated into FastAPI lifespan teardown in `hermes_cli/web_server.py`.
- Thread joins and cooperative cancel events ensure no orphaned supervisor threads or worker processes.

Observed verification:
- 150 passed tests in `tests/platform/execution/` and `tests/hermes_cli/test_civilization_web.py` (including full e2e lifecycle, pause/resume, and approval gate tests).
- 376 passed tests in frontend `web/` vitest suite; 0 TypeScript errors in `npm run typecheck`.
