"""Rack CLI parser construction."""

from __future__ import annotations

import argparse

from rack.progress_cli import add_progress_subparser


def build_parser() -> argparse.ArgumentParser:
    """Build the Rack CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Rack Test Framework CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  rack run              Run enabled strata
  rack run L0           Run L0_foundation stratum
  rack run --concern svg  Run only SVG-tagged subtests
  rack run L8_010::test_name  Run one test from a subtest
  rack list             List all strata
  rack list L0          List subtests in L0
  rack list --concern svg.text  List only concern-matching subtests
  rack status           Show last run status
  rack report           Generate HTML report
  rack audit            Audit manifest/test-suite drift
  rack new stratum L2_roundtrip       Create new stratum
  rack new subtest L2 003 my_test     Create new subtest
        """,
    )
    parser.add_argument("--version", action="store_true", help="Print version information and exit")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    list_parser = subparsers.add_parser("list", help="List strata and subtests")
    list_parser.add_argument("stratum", nargs="?", help="Stratum to list")
    list_parser.add_argument("--concern", help="Filter listed subtests by concern tag")

    run_parser = subparsers.add_parser("run", help="Run tests")
    run_parser.add_argument("stratum", nargs="?", help="Stratum or subtest to run")
    run_parser.add_argument("--all", action="store_true", help="Run all strata")
    run_parser.add_argument("--concern", help="Run only subtests tagged with concern")
    run_parser.add_argument("--lane", choices=["fast", "full", "strict"], help="Execution lane")
    run_parser.add_argument("--test", help="Run specific test name/expression")
    run_parser.add_argument("--progress", action="store_true", help="Enable Rack JSONL progress")
    run_parser.add_argument("--no-progress", action="store_true", help="Disable progress reporting")

    subparsers.add_parser("status", help="Show test status")
    subparsers.add_parser("report", help="Generate HTML report")
    subparsers.add_parser("refresh", help="Refresh stratum JSON from pytest data")

    inventory_parser = subparsers.add_parser("inventory", help="Show test case inventory")
    inventory_parser.add_argument("--orphans", action="store_true", help="Show only orphaned directories")

    add_progress_subparser(subparsers)

    audit_parser = subparsers.add_parser("audit", help="Audit Rack manifest/test-suite drift")
    audit_parser.add_argument("stratum", nargs="?", help="Optional stratum to audit")
    audit_parser.add_argument("--strict", action="store_true", help="Fail on missing inventory metadata")
    audit_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    audit_parser.add_argument("--signoff-stratum", action="append", help="Required signoff stratum")

    version_parser = subparsers.add_parser("version", help="Print version information")
    version_parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    new_parser = subparsers.add_parser("new", help="Create new stratum or subtest")
    new_subparsers = new_parser.add_subparsers(dest="new_type", help="What to create")

    new_stratum_parser = new_subparsers.add_parser("stratum", help="Create new stratum")
    new_stratum_parser.add_argument("name", help="Stratum name")

    new_subtest_parser = new_subparsers.add_parser("subtest", help="Create new subtest")
    new_subtest_parser.add_argument("stratum", help="Stratum name or prefix")
    new_subtest_parser.add_argument("seq", help="Sequence number")
    new_subtest_parser.add_argument("name", help="Subtest name")

    return parser
