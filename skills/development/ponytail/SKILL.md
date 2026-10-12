---
name: ponytail
description: "Lazy senior dev mode: YAGNI, stdlib first, -54% LOC."
version: "1.1.0"
author: "Dietrich Gebert (@DietrichGebert)"
license: "MIT"
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: "software-development"
    tags: ["ponytail", "yagni", "refactoring", "simplification"]
---

# Ponytail Skill

Applies the lazy senior developer decision ladder to write minimal, robust code.
Eliminates unrequested abstractions and reduces lines of code by ~54%.

## When to Use

- When implementing new code, features, bug fixes, or refactoring.
- When an agent is prone to over-engineering simple requests.
- When reviewing a diff to find unnecessary scaffolding and boilerplate to delete.

## Prerequisites

- Access to native Hermes file and execution tools (`read_file`, `patch`, `terminal`).

## How to Run

Stop at the first rung of the Decision Ladder that holds:

1. **Does this need to exist at all?** (YAGNI) -> Skip it.
2. **Already in this codebase?** -> Reuse it, do not re-implement.
3. **Stdlib does it?** -> Use standard library primitives.
4. **Native platform/browser feature?** -> Use native HTML/CSS/OS/DB constraints.
5. **Installed dependency?** -> Use existing dependencies, add zero new ones.
6. **Can it be one line?** -> One line.
7. **Only then:** -> The minimum working code that satisfies requirements.

## Ladder Discipline

The ladder is a procedure with a stopping point, not a vibe. These rules
make the choice auditable (clean-room adaptation of the "code ladder"
concept from over-engineering research):

- **Name the rung.** When you finish, state which rung you stopped at
  (e.g. "rung 3: stdlib — used `collections.Counter`"). A claim without a
  rung is not a decision.
- **Tie-breakers.** If two rungs would both work, the earlier (cheaper)
  rung wins. Decide quickly — deliberation is also cost.
- **Respect the declared runtime.** Before reaching for a newer stdlib
  function (rung 3), check the Python version the project declares.
  A function that does not exist on the project's runtime is not rung 3,
  it is rung 7 with extra risk.
- **Do not optimize what nobody measured.** Rung 6 ("can it be one line?")
  is about removing ceremony, not about micro-optimizing hot paths. If
  there is no measurement, there is no optimization task.
- **Rung evidence.** A reviewer (human or `ponytail-review`) may check the
  claim against the diff: an early-rung claim is believed only if the diff
  actually leans on that resource *and* the change is small. "Stdlib did
  it" does not describe forty lines of new classes.

## Rules

- No unrequested abstractions: no single-implementation interfaces, factories, or premature configs.
- No boilerplate for hypothetical future requirements.
- Deletion over addition: shortest working diff wins.
- Never compromise on input validation at trust boundaries, security, or data loss prevention.
- Fix root causes where callers route through, rather than patching symptoms in multiple files.

## Verification

- Run tests for affected modules using `terminal`.
- Leave behind a single runnable check or unit test for non-trivial logic.
- Optional static assist: `scripts/detect_overengineering.py` surfaces
  structural candidates (single-impl interfaces, one-product factories,
  delegate-only wrappers, ...) for the reviewer to judge. Advisory only —
  every shape has legitimate uses.

## Changelog

- 1.1.0: Added "Ladder Discipline" (rung declaration, tie-breakers,
  runtime-version check, no-unmeasured-optimization, rung evidence) and
  the optional `detect_overengineering.py` assist. No behavior change to
  the ladder itself.
