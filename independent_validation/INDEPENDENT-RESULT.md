# Independent GBrain v2 validation — 2026-10-10

Baseline reproduced in isolated integration branch: `473 passed in 44.25s` using `PYTHONPATH=. /usr/local/lib/haos-agent/venv/bin/python -m pytest -q tests/platform/context/memory tests/platform/memory tests/platform/context evals/gbrain_memory`.

Newly authored, independent holdout suite `test_new_adversarial_cases.py`: `7 failed, 4 passed`. All seven failures: false FACT_VERIFIED from invented, nonexistent or unvalidated evidence URIs (`log://inventado`, `https://example.invalid`, `file:///tmp/nao-existe`, `cmd://nao-executado`, `urn:unverified:123`). Explicit PT-BR/EN hypotheses passed all four cases.

This is independently reproduced against release candidate HEAD before any production changes. The previous frozen 120-case 100% accuracy is INSUFFICIENT. The text of a citation and the shape of a URI are not evidence. Classification must be fail-closed pending a trusted validation of the reference and its asserted content. The final canonical writer, Dream and reconciler need identical validation guarantees.

Current decision: NO-GO for production. Prepare backups/rollback as described in PREDEPLOY-RUNBOOK.md only after the regression is fixed and user authorizes deployment. Main dirty files and production databases remain untouched.
