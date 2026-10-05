# T0 / CG0 baseline findings

**Status:** deterministic offline harness exercised; model baseline not executed.

## T0 temporal

The 16-case corpus covers recent releases/incidents/prices/announcements, stale events, three conflicts, two undated cases and degraded/bypassed channels. Prompts do not contain the expected claims. Evidence dates are fixed against `2026-10-04T00:00:00Z`.

The observed default control emitted no claims, dates, citations or conflicts: `supported_recent=0`, `invented_dates=0`, `conflicts_present=0/3`, citation resolution is N/A (zero citations). This is the harness's empty-output control, **not** evidence about production/model behavior. It confirms that unknown dates remain unknown, and the scorer reports channel states; it does not prove factual accuracy.

**Decision:** NO-GO for production T1–T4 from T0 alone; the plan's required real baseline gap is unproven. Run one explicitly configured generative pilot or preserve existing-flow responses on the fixtures, then score those answers with the provided harness. No model/API result, cost, or latency is claimed.

## CG0 graph

Observed current codebase-wiki output over 7 fixture files: 4 Python files indexed, 9 nodes, 6 edges; Python symbol coverage 100%; Python import-edge recall proxy 2/2. Four TS/JS import/call edges in the fixture ground truth are absent (0 observed). Of three architectural questions, one question's required symbol nodes are all present. The question metric is a structural answerability proxy, not a natural-language QA test.

The codebase-wiki runner/indexer and its explicit `.ts/.js` exclusion were inspected. No external graph tool/dependency was installed.

**Decision:** GO only for a bounded TS/JS extractor experiment; NO-GO for selecting Graphify/Tree-sitter or shipping an extension until a candidate is measured against this same ground truth and compared for precision/recall, determinism, resource use, and maintenance cost. The small fixture establishes a capability gap, not that any implementation will improve useful architectural answers.

## Verification

- `HERMES_PYTHON=/usr/local/lib/haos-agent/venv/bin/python3 ./scripts/run_tests.sh evals/agent_skills_absorption/test_baselines.py` — 4 passed, 0 failed.
- `PYTHONPATH=. python3 evals/agent_skills_absorption/temporal.py` — executed; printed 16-case empty-output control metrics above.
- `PYTHONPATH=. python3 evals/agent_skills_absorption/codegraph.py` — executed; printed graph metrics above.
- No live model, network source, or statistical significance claim.
