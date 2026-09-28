from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from rack.parity import build_parity, format_parity_text
from rack.parity_html import render_parity_html

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUITE = ROOT / "tests" / "fixtures" / "declared_suite"
SCHEMA = ROOT / "docs" / "contracts" / "rack_parity_report.a0.schema.json"
UNITS = Path("L0_units")


def copy_suite(tmp_path: Path) -> Path:
    suite = tmp_path / "declared_suite"
    shutil.copytree(
        SOURCE_SUITE, suite, ignore=shutil.ignore_patterns("__pycache__", "rack_results")
    )
    return suite


def write_rows(suite: Path, file_stem: str, rows: list[tuple[str, str, str, str]]) -> None:
    """Write a per-file result the way rack run does: (test, case, implementation, outcome)."""
    path = suite / "rack_results" / "subtests" / f"{file_stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tests = [
        {
            "name": f"{test}[{case}-{implementation}]",
            "rack": {
                "test": test,
                "case": case,
                "implementation": implementation,
                "status": "implemented",
                "outcome": outcome,
                "detail": "",
                "differences": [],
            },
        }
        for test, case, implementation, outcome in rows
    ]
    path.write_text(json.dumps({"file": f"{file_stem}.py", "tests": tests}), encoding="utf-8")


def seeded_suite(tmp_path: Path) -> Path:
    suite = copy_suite(tmp_path)
    write_rows(
        suite,
        "test_L0_001_parse_duration",
        [
            ("L0_001", "hours_and_minutes", "python", "pass"),
            ("L0_001", "fractional_hours", "python", "pass"),
            ("L0_001", "hours_and_minutes", "shadow", "fail"),
            ("L0_001", "fractional_hours", "shadow", "deferred"),
        ],
    )
    write_rows(
        suite,
        "test_L0_002_vector_provenance",
        [("L0_002", "L0_001_parse_duration", "check", "pass")],
    )
    return suite


def at(value: object, *path: str | int) -> object:
    """Walk a JSON-shaped report, checking each container on the way."""
    for key in path:
        if isinstance(key, int):
            assert isinstance(value, list)
            value = value[key]
        else:
            assert isinstance(value, dict)
            value = value[key]
    return value


def test_parity_counts_statuses_cases_and_debt(tmp_path: Path) -> None:
    suite = seeded_suite(tmp_path)

    report = build_parity(suite, suite / "rack_results", ["L0_units"])

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(report)
    assert report["type"] == "rack.parity_report" and report["version"] == "a0"
    assert report["implementations"] == ["python", "shadow", "rust", "cpp", "wasm"]
    assert at(report, "totals", "implementations", "python") == {
        "tests": {"implemented": 3, "planned": 0, "suspended": 0, "not_applicable": 0},
        "cases": {"pass": 2, "fail": 0, "deferred": 0, "error": 0, "total": 7, "not_run": 5},
    }
    shadow = at(report, "totals", "implementations", "shadow", "cases")
    assert (at(shadow, "fail"), at(shadow, "deferred"), at(shadow, "not_run")) == (1, 1, 5)
    assert at(report, "totals", "implementations", "rust", "tests", "planned") == 1
    totals = at(report, "totals")
    assert (at(totals, "declared"), at(totals, "legacy"), at(totals, "invalid")) == (4, 1, 0)
    assert at(totals, "checks") == {"files": 1, "pass": 1, "fail": 0, "deferred": 0, "error": 0}

    debt = at(report, "debt")
    assert at(debt, "legacy_files") == ["L0_units/test_L0_004_legacy.py"]
    assert at(debt, "deferred") == [
        {"test": "L0_001", "implementation": "shadow", "case": "fractional_hours", "issue": "#2"}
    ]
    assert at(debt, "planned") == [{"test": "L0_001", "implementation": "rust", "note": "#1"}]
    assert at(debt, "suspended") == [
        {"test": "L0_001", "implementation": "cpp", "note": "port paused by policy"}
    ]
    assert at(debt, "failing") == [
        {"test": "L0_001", "case": "hours_and_minutes", "implementation": "shadow"}
    ]
    assert at(debt, "audit") == []
    tests = at(report, "tests")
    assert isinstance(tests, list)
    (check,) = [test for test in tests if at(test, "id") == "L0_002"]
    assert at(check, "case_count") == 1


def test_parity_reports_invalid_declarations_and_groups_by_concern(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    test_file = suite / UNITS / "test_L0_003_format_duration.py"
    text = test_file.read_text(encoding="utf-8")
    test_file.write_text(text.replace('"comparator": "exact"', '"compare": "exact"'), "utf-8")

    report = build_parity(suite, suite / "rack_results", ["L0_units"], group_by="concern")

    groups = at(report, "groups")
    assert isinstance(groups, list) and len(groups) == 1
    assert at(groups, 0, "name") == "fixture"
    assert at(report, "totals", "invalid") == 1
    assert at(report, "debt", "audit", 0, "code") == "invalid_declaration"


def test_parity_text_and_html(tmp_path: Path) -> None:
    report = build_parity(
        seeded_suite(tmp_path), tmp_path / "declared_suite" / "rack_results", ["L0_units"]
    )

    text = format_parity_text(report)
    assert "L0_units: 4 declared, 1 legacy, 0 invalid" in text
    assert "  shadow      3    0    0   0 |      7    0    1     1     0       5" in text
    assert "  deferred cases: 1 (#2 x1)" in text
    assert text.isascii()

    html = render_parity_html(report)
    assert "PARITY" in html and "Implementation grid" in html
    assert "L0_001 Duration parsing" in html
    assert "fractional_hours" in html and "port paused by policy" in html
    assert render_parity_html({"totals": {"declared": 0}}) == ""


def test_parity_command(tmp_path: Path) -> None:
    suite = seeded_suite(tmp_path)
    env = {key: value for key, value in os.environ.items()}
    env["RACK_TESTS_DIR"] = str(suite)

    def rack(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "rack", "parity", *args],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    as_json = rack("L0", "--format", "json")
    assert as_json.returncode == 0, as_json.stdout + as_json.stderr
    assert json.loads(as_json.stdout)["strata"] == ["L0_units"]

    as_text = rack("--by", "concern")
    assert as_text.returncode == 0, as_text.stdout + as_text.stderr
    assert "RACK PARITY by concern" in as_text.stdout

    assert rack("L7_missing").returncode == 1


def test_parity_reads_header_code_and_counts_a_native_test_as_one_case(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    source = (ROOT / "tests" / "L1_self_hosting" / "test_L1_004_plugin.py").read_text("utf-8")
    start = source.index('NATIVE_FORM_TEST = """') + len('NATIVE_FORM_TEST = """')
    text = source[start : source.index('"""', start)].replace(
        '"python": {"status": "implemented"},',
        '"python": {"status": "implemented", "code": [{"file": "suite_support/durations.py", '
        '"module": "suite_support.durations", "function": "parse_duration"}]},',
    )
    (suite / UNITS / "test_L0_007_plain_parse.py").write_text(text, encoding="utf-8")
    write_rows(
        suite,
        "test_L0_007_plain_parse",
        [
            ("L0_007", "hours_and_minutes", "python", "pass"),
            ("L0_007", "fractional_hours", "python", "pass"),
            ("L0_007", "long_form", "python", "pass"),
            ("L0_007", "test_l0_007_plain_parse.rs", "rust", "pass"),
        ],
    )

    report = build_parity(suite, suite / "rack_results", ["L0_units"])

    tests = at(report, "tests")
    assert isinstance(tests, list)
    detail = next(test for test in tests if at(test, "id") == "L0_007")
    assert at(detail, "case_count") == 3
    assert at(detail, "cells", "python", "code") == [
        {
            "file": "suite_support/durations.py",
            "module": "suite_support.durations",
            "function": "parse_duration",
        }
    ]
    assert at(detail, "cells", "rust", "cases") == {"test_l0_007_plain_parse.rs": "pass"}
    rust = at(report, "totals", "implementations", "rust", "cases")
    assert isinstance(rust, dict) and rust["pass"] == 1
