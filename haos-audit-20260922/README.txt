AUDITABLE LOCAL-CHANGE INVENTORY
Generated read-only from: /run/media/adriano/ADATA HD770G/HAOS-WEBUI-DSH-MIGRATED-WEBUI-backup-20260922
Checkout was not modified by this audit. Reports are stored outside the checkout.

Baseline
- branch: haos-compat-verification-20260922
- HEAD: 8129c155b659dfb6ecbc81c51539f820ac3b64f9
- changed tracked paths: 1893
- untracked paths: none observed in porcelain status
- mode-only changes: 1890
- content-only changes: 0
- content+mode changes: 3
- mode transition pairs: {('100644', '0740'): 1892, ('100755', '0740'): 1}

Interpretation
- Mode-only rows are consistent with a migration/backup filesystem-permission transformation, not code edits: their Git blob hashes match HEAD.
- Content-different rows are not automatically HAOS code; they require review. See classification.tsv and bootstrap-local.diff.
- The four requested HAOS commits are ancestors of HEAD; their patches are preserved as commit-*.patch.

Preservation procedure (safe; do not run in the principal checkout)
1. Keep the principal checkout untouched and stop any process that may write it.
2. Capture/verify this inventory and record the baseline HEAD and branch.
3. Create a separate clone or worktree from the principal repository, outside its directory.
4. In that isolated copy, preserve the working tree with `git diff --binary > haos-local.patch` and separately preserve mode metadata using `git diff --summary`; do not reset/clean the principal checkout.
5. Make a second isolated copy for HAOS review. Apply only reviewed content paths/patches there; keep mode-only migration artifacts separate.
6. Validate with `git diff --check`, blob hashes from manifest.json, and a post-copy status/hash comparison.
7. Only after independent review, commit selected HAOS content on a new branch; never use `git reset --hard`, `git clean`, checkout, or permission normalization on the principal checkout.

Files
- manifest.json: per-path HEAD/worktree blob hashes and modes.
- diff-summary.txt: exact `git diff --summary --no-renames` output.
- diff-numstat.tsv: exact `git diff --numstat --no-renames` output.
- status-porcelain-v1.txt: exact status snapshot.
- classification.tsv: deterministic mode-only vs content-different classification.
- bootstrap-local.diff: exact local bootstrap content diff.
- commit-*.patch: requested HAOS commit evidence.
