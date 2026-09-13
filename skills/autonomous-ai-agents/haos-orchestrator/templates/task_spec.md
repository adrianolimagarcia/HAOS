# <Task Title>

> Orchestrator field notes: keep this spec ≤ 4 KB. Oversized specs (6–7 KB) measurably
> fail — workers blow timeouts with code already on disk or never start implementing.
> Split into smaller tasks before dispatch. Reply with the real output; if a requirement
> cannot be met, say so. Do not weaken the asserts.

## Role
<!-- One narrow role: backend worker / frontend worker / reviewer / etc.
     Tools available to THIS worker come from harness role scoping (posture/toolset),
     never from prose instructions in this spec. -->

## Constraints
<!-- Hard rules: folders allowed to modify; folders forbidden (e.g. generated/, .env,
     infrastructure/); whether installs are allowed; model/provider if pinned. -->

## Files
<!-- Existing files to inspect FIRST, then exact paths to create/modify,
     including signatures, expected behavior and edge cases. -->

## Behavior
<!-- What the code must do, step by step. Concrete values where that helps.
     State required functions/classes with their contracts. -->

## Tests
<!-- Describe tests explicitly, with concrete inputs/expected outputs where useful.
     Do NOT weaken or skip asserts to go green — surface real failures instead. -->

## Verification
<!-- Exact commands that the orchestrator will re-run independently (CI parity):
     e.g. `scripts/run_tests.sh tests/...` + `python -m ruff check ...` + mypy.
     The worker's own "tests passed" is NOT verification; the orchestrator reruns
     these exact commands and remote CI remains the final gate. -->
