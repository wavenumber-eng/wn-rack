"""CLI adapter for Rack native audit."""

from __future__ import annotations

import json
from pathlib import Path

from rack.audit import audit_suite


def cmd_audit(args, tests_dir: Path) -> int:
    """Run Rack manifest audit checks."""
    signoff_strata = tuple(args.signoff_stratum or ())
    report = audit_suite(
        tests_dir,
        strict=args.strict,
        signoff_strata=signoff_strata,
        target_stratum=args.stratum,
    )

    if args.format == "json":
        print(json.dumps(report.to_json_data(), indent=2))
    else:
        print("\n" + "=" * 60)
        print("RACK AUDIT")
        print("=" * 60)
        if report.passed:
            print("\nNo audit failures.")
        else:
            print(f"\n{len(report.failures)} audit failure(s):")
            for failure in report.failures:
                print(f"  [{failure.code}] {failure.message}")

    return 0 if report.passed else 1
