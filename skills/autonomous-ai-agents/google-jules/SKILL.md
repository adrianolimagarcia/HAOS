---
name: google-jules
description: Delegate async coding tasks to Google Jules in the cloud.
version: 1.0.0
author: Adriano Lima Garcia (@adrianolimagarcia), Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    category: autonomous-ai-agents
    tags: [Coding-Agent, Google, Jules, GitHub, Cloud, Delegation]
    related_skills: [claude-code, hermes-agent, codex]
    config:
      jules:
        poll_interval_seconds: 15
        timeout_minutes: 30
        auto_merge: false
---

# Google Jules Skill

Delegate long-running coding and refactoring tasks to Google Jules in the cloud while continuing local work.

## When to Use
- Large-scale refactorings or comprehensive test additions that take significant time.
- Offloading independent issue fixes to Google's cloud sandbox without consuming local resources.
- Projects connected to GitHub with the Google Jules GitHub app installed.

## Prerequisites
- `JULES_API_KEY` set in `~/.hermes/.env`.
- Target repository configured with a valid GitHub `origin` remote.
- Google Jules GitHub app authorized for the target repository.

## How to Run
Use `terminal` to invoke the helper script:

```bash
python3 skills/autonomous-ai-agents/google-jules/scripts/jules_worker.py dispatch \
  --prompt "Refactor authentication retry logic and add comprehensive tests" \
  --sync-back
```

## Quick Reference
- **Check session status:**
  ```bash
  python3 skills/autonomous-ai-agents/google-jules/scripts/jules_worker.py status --session sessions/<SESSION_ID>
  ```
- **Sync completed branch to local repo:**
  ```bash
  git fetch origin <BRANCH> && git diff HEAD origin/<BRANCH>
  ```

## Procedure
1. Verify working directory status using `terminal` (`git status`).
2. Dispatch task with `jules_worker.py dispatch` specifying the prompt.
3. Track the returned `session_name` in memory or background tracker.
4. When finished, inspect the remote diff using `read_file` or `terminal`.
5. Integrate changes with `git merge` or `git cherry-pick`.

## Pitfalls
- **Uncommitted local changes:** Commit or stash changes before dispatching so remote Jules has access to current state.
- **Detached or local-only remotes:** Jules requires an accessible GitHub remote; local-only repositories cannot be synced.

## Verification
- Run `python3 skills/autonomous-ai-agents/google-jules/scripts/jules_worker.py --help` to confirm CLI parameters.
- Verify session state reports `SUCCEEDED` upon completion.
