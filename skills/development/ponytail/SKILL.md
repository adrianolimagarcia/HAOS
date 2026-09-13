---
name: ponytail
description: "Lazy senior dev mode: YAGNI, stdlib first, -54% LOC."
version: "1.0.0"
author: "Dietrich Gebert (@DietrichGebert)"
license: "MIT"
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

## Rules

- No unrequested abstractions: no single-implementation interfaces, factories, or premature configs.
- No boilerplate for hypothetical future requirements.
- Deletion over addition: shortest working diff wins.
- Never compromise on input validation at trust boundaries, security, or data loss prevention.
- Fix root causes where callers route through, rather than patching symptoms in multiple files.

## Verification

- Run tests for affected modules using `terminal`.
- Leave behind a single runnable check or unit test for non-trivial logic.
