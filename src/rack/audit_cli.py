"""CLI adapter for Rack native audit."""

from __future__ import annotations

import json
from pathlib import Path

from rack.audit import audit_suite
from rack.declaration_tally import DeclarationTally


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
        if report.declarations is not None:
            print_tally(
                report.declarations, list_undeclared=bool(getattr(args, "undeclared", False))
            )

    return 0 if report.passed else 1


def print_tally(tally: DeclarationTally, *, list_undeclared: bool = False) -> None:
    """Print how many test files are self-declared and which files fail which requirement."""
    print(
        f"\nDECLARATIONS: {tally.self_declared} of {tally.test_files} test files self-declared, "
        f"{tally.without_rack} without RACK, {len(tally.outside_strata)} outside strata"
    )
    width = max([7, *(len(stratum.name) for stratum in tally.strata)])
    print(f"  {'stratum'.ljust(width)}  declared  without RACK")
    for stratum in tally.strata:
        declared = f"{len(stratum.declared)}/{stratum.test_files}"
        enforced = "  require_declared" if stratum.require_declared else ""
        print(
            f"  {stratum.name.ljust(width)}  {declared:>8}  {len(stratum.without_rack):>12}{enforced}"
        )
        if list_undeclared:
            for name in stratum.without_rack:
                print(f"      {name}")
    print("  requirement         failing")
    for name, files in tally.requirements:
        print(f"  {name.ljust(18)}  {len(files):>7}")
        for file_name in files:
            print(f"      {file_name}")
    if tally.outside_strata:
        print("  outside strata (never audited; run only if a stratum directory contains them)")
        for file_name in tally.outside_strata:
            print(f"      {file_name}")
