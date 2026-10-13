---
name: ponytail-review
description: "Reviews code diffs to delete over-engineered code."
version: "1.1.0"
author: "Dietrich Gebert (@DietrichGebert)"
license: "MIT"
platforms: [linux, macos, windows]
metadata:
  hermes:
    category: "software-development"
    tags: ["ponytail", "review", "yagni", "code-deletion"]
---

# Ponytail Review Skill

Reviews working tree diffs and pull requests to eliminate over-engineered code.
Identifies unnecessary boilerplate, speculative abstractions, and delivers a deletion list.

## When to Use

- When reviewing a completed pull request or git diff before merging.
- When an implementer agent has produced an unnecessarily large diff for a simple bugfix.
- When auditing recent commits for premature optimization or dead code.

## Prerequisites

- Access to `terminal` (for running `git diff`) and `read_file`.

## How to Run

1. Inspect the patch or working tree diff using `terminal` (`git diff HEAD~1` or `git diff`).
2. Evaluate every added line against the Ponytail Decision Ladder:
   - Is this abstraction requested or speculative?
   - Can this be replaced by an existing utility or standard library function?
   - Can 10+ lines of custom logic be replaced with a native platform call?
3. Generate a concrete deletion list showing which files and lines should be removed.

## Trim Guard (deterministic)

Judgement deletes structure; a deterministic guard protects what must
never be cut. After producing the deletion list, run:

```bash
git diff HEAD~1 -- '*.py' | python3 scripts/trim_guard.py --diff
```

or, for two file versions:

```bash
python3 scripts/trim_guard.py --before old.py --after new.py
```

The trim guard counts guard signatures (input validation at trust
boundaries, parameterized queries, constant-time comparisons, HTML
escaping, per-row data guards, auth checks, accessibility hooks) on both
sides of the change and **refuses** it when the count decreases.

The five "never cut" categories, however many lines they take:

1. Input checks at trust boundaries (arriving input *and* departing data).
2. Data-loss prevention (per-row guards, transaction discipline).
3. Security (parameterized queries, constant-time compare, escaping, auth).
4. Accessibility (alt text, labels, ARIA).
5. Anything the user explicitly asked for.

If the guard refuses, restore the cut guards or justify each removal
individually — a smaller diff that drops a guard is a wrong diff, not a
better one.

## Output Format

- State what can be deleted, the estimated lines saved, and how to simplify the implementation.
- Pattern: `[code to remove] -> reason: [speculative / stdlib exists / native feature covers it].`
- Append the trim-guard verdict (`PASS` / `REFUSE` with delta).

## Changelog

- 1.1.0: Added deterministic "Trim Guard" step (`scripts/trim_guard.py`)
  and the explicit five-category "never cut" list. The skill judges;
  the script verifies.
