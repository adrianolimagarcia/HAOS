# Event-driven autonomy dashboard

The Framework page supervises the selected profile's durable queue. It does not run an AI investigation in a web request or expose an arbitrary command/action endpoint. Configuration remains CLI-managed; the panel reports `enabled` and `auto_apply` rather than silently enabling them.

## REST contract

All endpoints use the existing canonical dashboard session authentication, profile scope and same-origin mutation guard. Explicit `?profile=<name>` pins reads and mutations to their owning profile. Missing profiles and lexical symlinks (including SQLite sidecars) fail closed.

- `GET /api/framework/autonomy`: `{config:{enabled,auto_apply},queue:{available,paused,counts,budget_used,recent_jobs},service:{status,...}}`. Queue inspection uses the read-only API, never initializes absent storage or configuration. Recent jobs are bounded to 20. Heartbeat is read from `<profile>/agent/autonomy/service.json`; missing evidence is `unavailable`, timestamps older than 120 seconds (or invalid/future) are `stale`. PID alone is not worker-liveness evidence.
- `POST /api/framework/autonomy/pause`: strict `{paused:boolean,confirm:true}`. Persists pause without cancelling running work; only subsequent job claims stop. Operator writes may initialize queue storage.
- `POST /api/framework/autonomy/events`: strict `{type:"operator.investigation",source:"dashboard",severity:"debug|info|warning|error|critical",payload:{summary:string}}`. Summary must contain non-whitespace and at most 2000 characters. Unknown fields, arbitrary commands, action parameters and targets are rejected. The server derives the issue identity from the summary hash for deduplication. Response is the queue's `{accepted,reason,job_id}` admission receipt, not an investigation result.

Mutating queue construction respects validated profile `max_pending` and `cooldown_seconds`. Events are observations and untrusted text; they grant no execution authority.

## Supervision and evidence

The panel refreshes every 10 seconds **after** the preceding read settles, preventing overlapping polls. Abort cleanup cancels in-flight reads and writes on unmount; the page remounts on profile changes so prior-profile data and receipts never flash in another profile. Pause/resume requires an explicit confirmation dialog stating that running jobs continue.

Recent jobs expose actual recorded investigation/result data, plan IDs, action outcomes, pending authorization, usage and verification evidence in expandable records. A completed investigation is not proof of repair. Unknown/missing verification stays unknown; stale read errors are visible without breaking the surrounding Framework page.

Automatic adjustment, when configured, still requires existing exact autonomous authorization for the generated reversible framework report artifact. A changed report intent can require new authorization. No host/OS remediation is claimed. Jobs-per-hour and output-token bounds are not a dollar-cost guarantee.

## Validation

Native Vitest behavior tests cover confirmation, profile abort/remount, polling cadence and pinned controlled requests. `scripts/run_tests.sh` exercises real REST imports, canonical authentication, absent-storage GET, strict payload rejection, A→B→A profile isolation, heartbeat staleness and symlink refusal. Build with `npm -w web run build` from the isolated worktree; this writes only that tree's `hermes_cli/web_dist`. Building here does not deploy or update the current production GUI.
