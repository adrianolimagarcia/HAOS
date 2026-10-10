#!/usr/bin/env python3
"""HAOS Preflight CLI - Automated symbol validation and runtime drift detector.

Validates that all imported symbols in specified modules exist in target definitions,
and detects any drift (missing/modified files) between source repo and installed runtime/bundle.

Usage:
    python3 scripts/haos_preflight.py [--scope hermes/platform] [--prefix hermes.platform] [--target /path/to/runtime] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes.platform.diagnostics.preflight_drift import RuntimeDriftValidator


def main() -> None:
    parser = argparse.ArgumentParser(description="HAOS Preflight & Runtime Drift Validator")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Source repository root")
    parser.add_argument(
        "--target-runtime",
        default=None,
        help="Target bundle or runtime directory to validate against (read-only)",
    )
    parser.add_argument(
        "--scope",
        nargs="*",
        default=["hermes/platform"],
        help="Directory scopes to check (relative to repo root, e.g. hermes/platform)",
    )
    parser.add_argument(
        "--prefix",
        default="hermes.platform",
        help="Module prefix filter for checked imports (e.g. hermes.platform)",
    )
    parser.add_argument(
        "--check-drift",
        action="store_true",
        help="Compare files between repo and target runtime directory",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON report")

    args = parser.parse_args()

    validator = RuntimeDriftValidator(
        repo_root=Path(args.repo_root),
        target_runtime_dir=Path(args.target_runtime) if args.target_runtime else None,
    )

    report = validator.run_preflight(
        scope_dirs=args.scope,
        module_prefix=args.prefix,
        check_drift=args.check_drift,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print("=== HAOS Preflight & Runtime Drift Validator ===")
        print(f"Scanned files: {report.total_files_scanned}")
        print(f"Imports verified: {report.total_imports_checked}")

        if report.missing_symbols:
            print(f"\n❌ Found {len(report.missing_symbols)} missing/unresolved symbol(s):")
            for mismatch in report.missing_symbols:
                print(f"  - [{mismatch.importer_file}] cannot import '{mismatch.symbol_name}' from '{mismatch.target_module}'")

        if report.file_drifts:
            print(f"\n⚠ Found {len(report.file_drifts)} file drift(s) vs target runtime:")
            for drift in report.file_drifts:
                print(f"  - [{drift.status}] {drift.rel_path}")

        if report.errors:
            print(f"\n❌ Syntax / Parse errors ({len(report.errors)}):")
            for err in report.errors:
                print(f"  - {err}")

        if report.valid:
            print("\n✅ All symbols and runtime contracts passed verification!")
        else:
            print("\n❌ Preflight validation failed!")

    sys.exit(0 if report.valid else 1)


if __name__ == "__main__":
    main()
