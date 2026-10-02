# Operational framework dashboard API

`/framework` is a lazy route in the canonical FastAPI dashboard SPA. Primary chat and dirty MemoryGraph/civilization sources are unchanged.

All REST routes use canonical `_require_token` authority (existing ephemeral token or authenticated dashboard gate). Each accepts `?profile=<name>` using canonical `_config_profile_scope`; invalid/missing profiles and symlinked framework records fail closed. Frontend pins explicit profiles and remounts on profile changes. Host telemetry is labeled host-wide; WAL and framework records belong to the selected profile.

## Routes

- GET `/api/framework/status`: state and capability flags.
- GET `/api/framework/telemetry`: actual metrics, source errors and unknowns.
- GET `/api/framework/plans?limit=20`: persisted plans, limit 1–100.
- GET `/api/framework/hierarchy`: bounded domain-worker result and canonical TaskSpec hierarchy.
- GET `/api/framework/actions?limit=20`: actual action receipts (rollback evidence) and cycle summaries.
- POST `/api/framework/run`: `{dry_run:true,create_report:false}`. `create_report:true` previews the canonical reversible operational-report JSON artifact.
- POST `/api/framework/grant`: `{plan_id,step_id,confirm:true,autonomous:false}` exact canonical policy grant with rollback permission.
- POST `/api/framework/approve`: `{plan_id,step_id,confirm:true,ttl_seconds:300}` approval binds saved intent and expires (1–3600 seconds).
- POST `/api/framework/apply`: `{plan_id,approval_ids:[],mode:"assisted",confirm:true}`; mode may be `autonomous` only with an explicit exact autonomous policy grant. Saved IDs only, never uploaded approval objects, targets, or action parameters. Failed/refused execution returns HTTP409, not success.

Browser mutations reject mismatched/malformed Origin. Gated mutations require Origin, including bearer callers; loopback non-browser canonical-token callers may omit it. No new auth scheme or role system.

Action scope is ONLY `<selected-profile>/agent/artifacts/operational-report.json`; this is not host tuning. Core validates exact grants, intent-bound expiring approvals, postconditions, rollback, and one-use plan receipts. Failed attempts are also consumed. UI displays exact parameters and requires a second explicit confirmation before grant/approval/apply. No approval is persisted in browser storage.

## Verified locally

- `scripts/run_tests.sh tests/hermes_cli/test_framework_web.py`: 2 integration tests passed, including real saved report preview→grant→approve→apply, exact artifact existence and finished receipt, replay refusal, expiry, tampering, cross-profile approval refusal, unknown profile/symlink refusal, invalid input, unauthenticated endpoints, and gated mutation Origin guard.
- `npm run test -- src/lib/framework.test.ts src/lib/api.test.ts src/components/FrameworkPlanControls.test.tsx` from `web`: 11 passed; canonical auth/scoped requests and explicit exact-intent confirmation.
- `npm run build` from `web`: TypeScript and Vite succeeded. Production artifacts: `hermes_cli/web_dist/index.html` and `hermes_cli/web_dist/assets/`.

Workspace build includes existing dirty unrelated frontend files because Vite builds the whole SPA. Release acceptance must rebuild the parent's clean scoped worktree before publishing. No deployment, service restart, external custom WebUI edit, VM mutation, secret access, commit, host tuning or ISO was performed by this dashboard subtask. Public routing/access and live browser verification remain parent/deployment-agent responsibilities.
