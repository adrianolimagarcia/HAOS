# HAOS Harness 2.0 Engineering Principles & Architecture

Documented architectural guardrails, verification layers, and recovery rules derived from the Harness 2.0 methodology.

## 1. Principles

1. **Constraints Over Instructions**: A prompt saying "always write tests" is not enough. The harness environment must enforce verification before work is marked complete.
2. **Multi-Layered Verification**: Code correctness requires multiple verification layers:
   - **Git Guardrails**: Block modifications to forbidden files (`.env`, secrets, sensitive configs, generated bundles).
   - **Syntax & Style**: Python bytecode compilation check (`py_compile`) and type checks.
   - **Test Suite**: Automated execution via `scripts/run_tests.sh`.
3. **Structured Recovery Loop**: When tests fail, provide actionable errors (`what failed`, `why`, `recovery suggestions`).
4. **Anti-Loop Boundaries**: Impose a hard limit on retries (`MAX_ATTEMPTS = 3`). Do not permit infinite repair loops with identical errors.
5. **Separation of Planning and Execution**: Outline expected impacted files before editing; verify against actual diff before sign-off.

## 2. Verification Harness

The verification suite runner is located at `scripts/haos_verify.py`:

```bash
# Run verification with targeted test suite
python3 scripts/haos_verify.py --tests tests/path/to/test.py --json

# Run rapid syntax & git guardrail checks without running full tests
python3 scripts/haos_verify.py --skip-tests --json
```

## 3. Skill Integration

The skill `skills/autonomous-ai-agents/haos-dev-harness/SKILL.md` guides autonomous agents in adopting the Harness 2.0 cycle:
Plan → Act → Verify → Recover → Finalize.
