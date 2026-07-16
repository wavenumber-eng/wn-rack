from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from jsonschema import Draft202012Validator

from rack.audit import audit_suite


ROOT = Path(__file__).resolve().parents[2]


def test_audit_suite_passes_synchronized_suite(tmp_path: Path) -> None:
    write_valid_suite(tmp_path)

    report = audit_suite(tmp_path)

    assert report.passed
    assert report.to_json_data()["type"] == "rack.audit_report"
    assert report.to_json_data()["version"] == "a0"


def test_audit_suite_detects_manifest_drift(tmp_path: Path) -> None:
    write_valid_suite(tmp_path)
    write_file(tmp_path / "L0_foundation" / "test_L0_002_extra.py", "def test_extra(): pass\n")
    (tmp_path / "L0_foundation" / "test_L0_001_demo.py").unlink()

    report = audit_suite(tmp_path)

    codes = {failure.code for failure in report.failures}
    assert "missing_discovered_subtest" in codes
    assert "missing_declared_subtest_file" in codes


def test_audit_suite_detects_duplicate_strata_order(tmp_path: Path) -> None:
    write_valid_suite(tmp_path)
    write_file(
        tmp_path / "rack.toml",
        """
        [rack]
        name = "Demo"

        [strata]
        order = ["L0_foundation", "L0_foundation", "L99_signoff"]
        """,
    )

    report = audit_suite(tmp_path, strict=True)

    codes = {failure.code for failure in report.failures}
    assert "duplicate_stratum" in codes


def test_audit_suite_detects_duplicate_subtests_and_strict_metadata(
    tmp_path: Path,
) -> None:
    write_valid_suite(tmp_path)
    append_file(
        tmp_path / "L0_foundation" / "STRATUM.toml",
        """

        [[subtests]]
        id = "L0_001"
        file = "test_L0_001_demo.py"
        name = "Duplicate"
        description = "Duplicate"
        """,
    )

    report = audit_suite(tmp_path, strict=True)

    codes = {failure.code for failure in report.failures}
    assert "duplicate_subtest_id" in codes
    assert "duplicate_subtest_file" in codes
    assert "missing_test_cases" in codes
    assert "missing_test_case_type" in codes


def test_audit_json_report_matches_schema(tmp_path: Path) -> None:
    write_valid_suite(tmp_path)
    report = audit_suite(tmp_path)
    schema = json.loads(
        (ROOT / "docs" / "contracts" / "rack_audit_report.a0.schema.json").read_text(
            encoding="utf-8"
        )
    )

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(report.to_json_data())


def test_rack_audit_cli_returns_nonzero_and_json_for_failures(tmp_path: Path) -> None:
    write_valid_suite(tmp_path)
    write_file(tmp_path / "L0_foundation" / "test_L0_002_extra.py", "def test_extra(): pass\n")

    result = subprocess.run(
        [sys.executable, "-m", "rack", "audit", "--format", "json"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["type"] == "rack.audit_report"
    assert payload["passed"] is False
    assert payload["failures"][0]["code"] == "missing_discovered_subtest"


def test_rack_audit_cli_with_stratum_returns_json_for_invalid_rack_toml(
    tmp_path: Path,
) -> None:
    write_file(tmp_path / "rack.toml", "[strata\n")

    result = subprocess.run(
        [sys.executable, "-m", "rack", "audit", "L0", "--format", "json"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["type"] == "rack.audit_report"
    assert {failure["code"] for failure in payload["failures"]} >= {
        "invalid_toml",
        "unknown_stratum",
    }


def write_valid_suite(root: Path) -> None:
    write_file(
        root / "rack.toml",
        """
        [rack]
        name = "Demo"

        [strata]
        order = ["L0_foundation", "L99_signoff"]
        """,
    )
    write_stratum(root, "L0_foundation", "L0_001", "test_L0_001_demo.py", ["core"])
    write_stratum(root, "L99_signoff", "L99_001", "test_L99_001_signoff.py", ["signoff"])
    write_file(root / "L0_foundation" / "test_L0_001_demo.py", "def test_demo(): pass\n")
    write_file(root / "L99_signoff" / "test_L99_001_signoff.py", "def test_signoff(): pass\n")


def write_stratum(
    root: Path,
    stratum: str,
    subtest_id: str,
    file_name: str,
    concerns: list[str],
) -> None:
    quoted_concerns = ", ".join(repr(concern).replace("'", '"') for concern in concerns)
    write_file(
        root / stratum / "STRATUM.toml",
        f"""
        name = "{stratum}"
        order = 0
        description = "Demo stratum"
        enabled = true
        concerns = [{quoted_concerns}]

        [[subtests]]
        id = "{subtest_id}"
        file = "{file_name}"
        name = "Demo"
        description = "Demo"
        test_cases = "cases/demo"
        test_case_type = "algorithmic"
        """,
    )


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(text).lstrip(), encoding="utf-8")


def append_file(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(dedent(text))
