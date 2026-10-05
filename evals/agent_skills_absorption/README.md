# Agent Skills Absorption — T0 / CG0 baselines

These are small offline decision baselines, not model-quality benchmarks. No generative model or network call is made by default.

## T0: temporal contract

- Frozen corpus: 16 answer-free prompts plus synthetic dated/undated evidence fixtures, conflicts, and explicit ok/degraded/bypassed channel coverage.
- Scorer: `python evals/agent_skills_absorption/temporal.py`. Optional pilot answer JSON list shape:
  `[{"case_id":"T0-01","citation_ids":["E01"],"claimed_date":"2026-09-20T10:00:00Z","conflict_ids":[]}]`.
- The default result is an empty-output harness control (not current-product/model performance): 0/16 supported recent claims, 0 invented dates, no conflicts exposed, and no citations to resolve. It only confirms scorer behavior and the no-claim floor.
- A model pilot requires an explicitly selected model/API and should run once with the same fixtures, preserving raw answers and cost/latency. It is intentionally not claimed as reproduced or CI-stable here.

## CG0: current graph

- Corpus: four Python modules and three TS/JS files, with hand-auditable symbol/import/call/type ground truth and three architectural questions.
- Run: `PYTHONPATH=. python evals/agent_skills_absorption/codegraph.py`.
- Current Python indexer measured 4/4 files; 100% Python symbol coverage; 2/2 Python imports resolved (proxy); 4 TS/JS ground-truth imports/calls absent; 1/3 questions have all target symbols represented.
- No Graphify, Tree-sitter, or new dependencies used. This tests current graph ingestion only, not the benefit/cost of any extension.

## Interpretation / decision

- T0: **NO-GO for temporal production change based on this baseline alone**. The frozen fixture scorer can validate dates, citation IDs, conflicts, and coverage once a real baseline answer set exists; this mechanical no-output control cannot establish a model/product gap. Next: one separately recorded model pilot or capture of existing live-flow outputs, then compare with the same corpus.
- CG0: **GO to a bounded TS/JS extension experiment; NO-GO to dependency adoption now**. The current engine demonstrably omits the polyglot fixtures, while Python coverage is complete. Any extension needs a separate candidate extractor and quality/resource comparison against the same ground truth before adoption.

## Test execution

`HERMES_PYTHON=/usr/local/lib/haos-agent/venv/bin/python3 ./scripts/run_tests.sh evals/agent_skills_absorption/test_baselines.py`
