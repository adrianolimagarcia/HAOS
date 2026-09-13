---
name: ponytail-review
description: "Reviews code diffs to delete over-engineered code."
version: "1.0.0"
author: "Dietrich Gebert (@DietrichGebert)"
license: "MIT"
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

## Output Format

- State what can be deleted, the estimated lines saved, and how to simplify the implementation.
- Pattern: `[code to remove] -> reason: [speculative / stdlib exists / native feature covers it].`
