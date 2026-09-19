# Security Review Ledger

Record of security findings that were actually investigated, with the evidence that decided them.

[`SECURITY.md`](../../SECURITY.md) is the **policy** — trust model, scope, disclosure. This file is
the **ledger**: one entry per finding, written so a later reader can re-derive the conclusion
without re-doing the investigation. `network-egress-isolation.md` is a hardening deep-dive, not a
finding.

The ledger exists because a fix commit message is not evidence. `fix: validate webhook secret`
records that something changed; it does not record which boundary was crossed, who was exposed, how
the defect was reproduced, or — the field that matters most — **what was never tested**. Without
that last field a reader cannot tell a thorough fix from a plausible one, and the next reviewer
re-treads the same ground or, worse, trusts a conclusion the author never reached.

---

## What belongs here

A finding is in scope once it has been **reproduced or explicitly ruled out**. Entries are added
whether or not the defect was real: a confirmed `not-reproducible` or `by-design` disposition is a
result, and recording it stops the next person from re-investigating the same suspicion.

## What does not belong here

- **Unconfirmed suspicions.** A pattern that looks dangerous but was not exercised belongs in an
  issue, not here. The ledger's value is that every entry was decided.
- **Hardening ideas with no demonstrated boundary crossing.** Those are PRs or issues.
- **Anything requiring the reader to trust the author's summary.** If there is no reproduction, the
  entry is not ready.
- **Credentials, live tokens, private user data, or working exploit payloads against third-party
  systems.** Redact to the minimum that makes the reproduction reproducible.

---

## Required fields

Every entry carries all of these. An entry missing any of them is incomplete, not "brief".

| Field | Why it exists |
|---|---|
| `Reviewed` | The date the investigation happened, and the `Baseline` commit it was done against. A conclusion is only valid for a tree. |
| `Introduced` | The commit or PR that introduced the defect, when it can be established. Distinguishes a regression from a long-standing gap, and bounds who is affected. `unknown` is an acceptable answer; a guess is not. |
| `Disposition` | One of the vocabulary below. Forces the question "did we decide, or did we assume?" |
| `Severity` | Impact **and the reasoning that bounds it**. "High severity demonstrated locally; critical impact conditional on X" is a real answer. A bare label is not. |
| `Affected users` | Who must act, and under what configuration. A defect behind an off-by-default flag affects different people than one on the default path. |
| `Boundary` | The trust boundary that was crossed, named concretely. This is the field that makes a finding reviewable: it states what authority the attacker gained that they were not meant to have. |
| `Anchors` | `file:line` for the code that decides the behavior — not the whole call chain, the deciding line. |
| `Evidence` | How it was reproduced, with the observed marker. Say what was real production code and what was stubbed. |
| `Remediation` | What changed, and why that change and not a broader one. |
| `Verification` | The exact commands run, and their observed results. |
| `Limits` | **What was not tested, and what that leaves open.** See below. |

### The `Limits` field

This is the field the ledger is built around, and the one most likely to be skipped.

State plainly: which runtime was exercised and which was not; whether the live transport preserved
the crafted input; whether the impact is bounded by a container, a mount, or nothing; and what a
fuller reproduction would require. "Confirmed high severity" and "confirmed high severity, and the
root-escape chain was not tested" are different claims, and only the second is honest about its
evidence.

A finding is not closed on the strength of a fix. It is closed when the reader can see exactly how
far the evidence reaches.

## Disposition vocabulary

| Value | Meaning |
|---|---|
| `confirmed` | Reproduced. The defect is real and the fix is in the tree. |
| `confirmed-not-fixed` | Reproduced, real, and deliberately not fixed yet — with the reason. |
| `not-reproducible` | The claimed behavior was exercised and did not occur on the stated baseline. |
| `by-design` | The behavior is the intended contract. Say why the alternative is worse. |

## Status of this ledger

**Empty.** No finding has been recorded yet.

This is not a statement that the codebase is free of defects, and it must not be read as one. It
means only that no investigation has been written up in this format. The surfaces that would be
expected to produce the first entries — the ~20 gateway platform adapters, the plugin catalog
admission path, the approval/guard layer under `tools/`, and the terminal backends — have had no
entry filed against them here.

---

## Findings

_None recorded._

---

## Template

Copy this into the Findings section. Delete no field; write `none` or `not tested` instead of
removing one.

````markdown
## SEC-NNN: <one-line description of the boundary that was crossed>

- **Reviewed:** YYYY-MM-DD. **Baseline:** `<40-char sha>`. **Introduced:** `<sha>` / `unknown`.
- **Disposition:** `confirmed` | `confirmed-not-fixed` | `not-reproducible` | `by-design`.
- **Severity:** <label>. <The reasoning that bounds it, including what it is conditional on.>
- **Affected users:** <who is exposed, and under which configuration; say if the path is off by default.>
- **Boundary:** <the trust boundary crossed, and the authority the attacker gained.>

### Anchors

- `<file>:<line>` — <the line that decides the behavior>

### Evidence

<What was exercised. State which parts were real production code and which were stubbed.>

```bash
<the command that reproduces it>
```

- Result: <observed output, including the marker that distinguishes pass from fail>
- Controls: <the negative controls that show the test can fail>

### Remediation

<What changed. Why this change rather than a broader one. What was deliberately left alone.>

### Verification

```bash
<the commands run after the fix>
```

- Result: <observed output>

### Limits

<What was NOT tested. Which runtime was not exercised. Whether live transport preserves the crafted
input. Whether the impact is bounded by a container, a mount, or nothing. What a fuller
reproduction would need.>
````
