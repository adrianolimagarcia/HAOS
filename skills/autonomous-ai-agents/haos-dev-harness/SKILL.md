---
name: haos-dev-harness
description: Multi-layer verification and recovery loop for HAOS.
version: 1.0.0
author: Adriano Lima + Hermes Agent
license: MIT
platforms: [linux, macos]
category: autonomous-ai-agents
tags: [haos, harness, verification, quality-gate, devops]
---

# HAOS Dev Harness Skill

Executes multi-layered verification, guardrail enforcement, and structured recovery loops for software engineering tasks in HAOS.

## When to Use

Use this skill when:
- Implementing, refactoring, or fixing code in the HAOS repository.
- Preparing work before completing a task or submitting changes for review.
- Verifying whether file modifications touch forbidden paths or break tests.

## Prerequisites

- Workspace access via `read_file`, `patch`, `write_file`, and `terminal`.
- The verification harness runner at `scripts/haos_verify.py`.
- Git repository initialized in the current workspace.

## How to Run

Execute the verification harness using `terminal`:

```bash
python3 scripts/haos_verify.py --tests <path_to_test> --json
```

Or without tests for rapid syntax and git guardrail checks:

```bash
python3 scripts/haos_verify.py --skip-tests --json
```

## Quick Reference

| Layer | Checked By | Fail Condition | Action on Failure |
|---|---|---|---|
| Git Guardrails | `scripts/haos_verify.py` | Sensitive files (`.env`, keys) modified | Revert modifications with `terminal` |
| Syntax / Linter | `py_compile` | Compilation / syntax errors | Fix syntax errors via `patch` |
| Test Suite | `scripts/run_tests.sh` | Unit or contract tests red | Inspect stdout/stderr and fix logic |
| Recovery Loop | Loop logic | Errors repeat or pass attempt cap (3x) | Escalate or report concrete blocker |

## Procedure

1. **Context & Planning**: Review the task specifications and identify target files before making any modifications.
2. **Execution**: Apply targeted edits using `patch` or `write_file`. Avoid sweeping rewrites.
3. **Verification**: Run `python3 scripts/haos_verify.py --tests <test_path> --json` via `terminal`.
4. **Structured Recovery**:
   - If `success` is `true`, proceed to conclude the task.
   - If `success` is `false`, inspect `recovery_hints` and test failures.
   - Adjust the code to resolve the specific failure.
   - Re-run verification (maximum of 3 attempts).
5. **Final Review**: Ensure `git status` reflects only intended changes before final sign-off.

## Pitfalls

- Do not commit changes if `scripts/haos_verify.py` reports git guardrail violations.
- Do not repeat identical failed attempts: analyze the concrete error message on each cycle.
- Do not bypass verification when tests exist for the touched components.

## Verification

Run the verification harness directly:

```bash
python3 scripts/haos_verify.py --skip-tests
```

Confirm that the output reports all verification layers passed.
