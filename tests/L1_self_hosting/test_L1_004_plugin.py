from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUITE = ROOT / "tests" / "fixtures" / "declared_suite"
TEST_L0_001 = Path("L0_units") / "test_L0_001_parse_duration.py"
VECTORS_L0_001 = Path("L0_units") / "vectors" / "L0_001_parse_duration.json"

Rows = dict[str, tuple[str, str, str]]


def copy_suite(tmp_path: Path) -> Path:
    suite = tmp_path / "declared_suite"
    shutil.copytree(SOURCE_SUITE, suite, ignore=shutil.ignore_patterns("__pycache__"))
    return suite


def run_suite(
    suite: Path, *args: str, lane: str = ""
) -> tuple[subprocess.CompletedProcess[str], Rows]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("RACK_LANE", "WN_RACK_LANE", "WN_TEST_LANE", "RACK_IMPL")
    }
    if lane:
        env["RACK_LANE"] = lane
    report = suite / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            ".",
            "-p",
            "no:cacheprovider",
            "-q",
            "-rs",
            "--json-report",
            f"--json-report-file={report}",
            *args,
        ],
        cwd=suite,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, read_rows(report)


def read_rows(report: Path) -> Rows:
    payload = json.loads(report.read_text(encoding="utf-8"))
    rows: Rows = {}
    for test in payload["tests"]:
        properties = {
            key: value for item in test.get("user_properties", []) for key, value in item.items()
        }
        name = test["nodeid"].split("::")[-1]
        rows[name] = (
            test["outcome"],
            properties.get("rack_outcome", ""),
            properties.get("rack_detail", ""),
        )
    return rows


def outcomes(rows: Rows) -> dict[str, tuple[str, str]]:
    return {name: (row[0], row[1]) for name, row in rows.items()}


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_default_run_expands_cases_by_implementation(tmp_path: Path) -> None:
    result, rows = run_suite(copy_suite(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert outcomes(rows) == {
        "L0_001[hours_and_minutes-python]": ("passed", "pass"),
        "L0_001[hours_and_minutes-shadow]": ("passed", "pass"),
        "L0_001[hours_and_minutes-rust]": ("skipped", "planned"),
        "L0_001[hours_and_minutes-cpp]": ("skipped", "suspended"),
        "L0_001[fractional_hours-python]": ("passed", "pass"),
        "L0_001[fractional_hours-shadow]": ("xfailed", "deferred"),
        "L0_001[fractional_hours-rust]": ("skipped", "planned"),
        "L0_001[fractional_hours-cpp]": ("skipped", "suspended"),
        "L0_001[long_form-python]": ("skipped", "skipped"),
        "L0_001[long_form-shadow]": ("skipped", "skipped"),
        "L0_001[long_form-rust]": ("skipped", "planned"),
        "L0_001[long_form-cpp]": ("skipped", "suspended"),
        "L0_002[L0_001_parse_duration-check]": ("passed", "pass"),
        "L0_002[L0_003_format_duration-check]": ("passed", "pass"),
        "L0_003[ninety_minutes-python]": ("passed", "pass"),
        "L0_003[ninety_minutes-shadow]": ("passed", "pass"),
        "L0_003[zero-python]": ("passed", "pass"),
        "L0_003[zero-shadow]": ("passed", "pass"),
        "L0_006[ninety_minutes-python]": ("passed", "pass"),
        "L0_006[ninety_minutes-shadow]": ("passed", "pass"),
        "L0_006[zero-python]": ("passed", "pass"),
        "L0_006[zero-shadow]": ("passed", "pass"),
        "test_legacy_style_still_runs": ("passed", ""),
    }
    assert "lane full" in rows["L0_001[long_form-python]"][2]
    assert rows["L0_001[hours_and_minutes-rust]"][2] == "not ported yet"


def test_suite_skip_markers_are_recorded_as_skipped_rows(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    (suite / "conftest.py").write_text(
        "import pytest\n\n\n"
        "def pytest_collection_modifyitems(config, items):\n"
        "    for item in items:\n"
        "        if item.get_closest_marker('shadow_off') or item.name.endswith('-shadow]'):\n"
        "            item.add_marker(pytest.mark.skip(reason='shadow disabled by suite'))\n",
        encoding="utf-8",
    )

    result, rows = run_suite(suite)

    assert result.returncode == 0, result.stdout + result.stderr
    assert rows["L0_001[hours_and_minutes-shadow]"] == (
        "skipped",
        "skipped",
        "shadow disabled by suite",
    )
    skip_lines = [line for line in result.stdout.splitlines() if line.startswith("SKIPPED")]
    assert skip_lines and not any("plugin.py" in line for line in skip_lines)


def test_lane_and_implementation_selection(tmp_path: Path) -> None:
    result, rows = run_suite(copy_suite(tmp_path), "--rack-impl", "shadow,rust", lane="full")

    assert result.returncode == 0, result.stdout + result.stderr
    assert rows["L0_001[long_form-shadow]"][:2] == ("passed", "pass")
    assert rows["L0_001[long_form-python]"][:2] == ("skipped", "skipped")
    assert rows["L0_001[long_form-python]"][2] == "implementation not selected"
    assert rows["L0_001[long_form-cpp]"][:2] == ("skipped", "skipped")
    # A selected planned implementation without an adapter reports an error without gating.
    assert rows["L0_001[long_form-rust]"][:2] == ("xfailed", "error")
    assert "no adapter" in rows["L0_001[long_form-rust]"][2]
    assert rows["L0_002[L0_001_parse_duration-check]"][:2] == ("passed", "pass")


def test_deferral_must_match_exactly_and_is_removed_once_passing(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    replace_in(suite / TEST_L0_001, '"actual": 3600', '"actual": 3601')

    result, rows = run_suite(suite)

    assert result.returncode == 1
    assert rows["L0_001[fractional_hours-shadow]"] == (
        "failed",
        "fail",
        "differences do not match the declared deferral",
    )

    suite = copy_suite(tmp_path / "second")
    replace_in(suite / VECTORS_L0_001, '"text": "1.5h"', '"text": "90m"')

    result, rows = run_suite(suite)

    assert result.returncode == 1
    assert rows["L0_001[fractional_hours-shadow]"][:2] == ("failed", "fail")
    assert "remove its deferral" in rows["L0_001[fractional_hours-shadow]"][2]


def test_failures_carry_typed_differences(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    replace_in(
        suite / VECTORS_L0_001, '"expect": {"seconds": 5400}}', '"expect": {"seconds": 5401}}'
    )

    result, _rows = run_suite(suite)
    payload = json.loads((suite / "report.json").read_text(encoding="utf-8"))
    (row,) = [t for t in payload["tests"] if t["nodeid"].endswith("[hours_and_minutes-python]")]
    properties = {key: value for item in row["user_properties"] for key, value in item.items()}

    assert result.returncode == 1
    assert properties["rack_outcome"] == "fail"
    assert properties["rack_differences"] == [
        {"path": ["seconds"], "kind": "value", "expected": 5401, "actual": 5400}
    ]
    assert "['seconds'] value: expected=5401 actual=5400" in result.stdout
    assert "L0_001 hours_and_minutes [python]: 1 difference(s)" in result.stdout
    assert "plugin.py" not in result.stdout


def test_invalid_declarations_fail_collection(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    replace_in(suite / VECTORS_L0_001, '"id": "fractional_hours"', '"id": "fractional"')

    result, _rows = run_suite(suite)

    assert result.returncode != 0
    assert "deferral for unknown case 'fractional_hours'" in result.stdout

    suite = copy_suite(tmp_path / "second")
    replace_in(suite / VECTORS_L0_001, '"lane": "full"', '"lane": "nightly"')

    result, _rows = run_suite(suite)

    assert result.returncode != 0
    assert "case lanes ['nightly']" in result.stdout


def test_legacy_file_mentioning_rack_text_is_not_captured(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    legacy = suite / "L0_units" / "test_L0_005_mentions_rack.py"
    legacy.write_text(
        'SOURCE = """\nRACK = {"id": "L0_099"}\n"""\n\n\n'
        "def test_mentions_rack() -> None:\n    assert SOURCE\n",
        encoding="utf-8",
    )

    result, rows = run_suite(suite)

    assert result.returncode == 0, result.stdout + result.stderr
    assert rows["test_mentions_rack"][:2] == ("passed", "")


BUDGET_TEST = '''"""parse_duration stays within its time budget."""

RACK = {
    "id": "L0_005",
    "title": "Duration parsing budget",
    "cases": {"file": "vectors/L0_001_parse_duration.json"},
    "expect": {
        "source": "budget",
        "metric": "wall_seconds",
        "max": 60,
        "max_ratio": 0.000001,
        "relative_to": "python",
    },
    "implementations": {"python": "implemented", "shadow": "implemented"},
}


def run(case, impl):
    return impl.batch([{"op": "parse_duration", "text": case.inputs["text"]}])
'''


def test_budget_expectation_measures_run_time(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    (suite / "L0_units" / "test_L0_005_parse_budget.py").write_text(BUDGET_TEST, encoding="utf-8")

    result, rows = run_suite(suite)

    # The baseline row skips its own ratio check; the other row exceeds a near-zero ratio.
    assert rows["L0_005[hours_and_minutes-python]"][:2] == ("passed", "pass")
    assert rows["L0_005[hours_and_minutes-shadow]"][:2] == ("failed", "fail")
    assert result.returncode == 1
