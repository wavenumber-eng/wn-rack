from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from rack import cli


ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUITE = ROOT / "tests" / "fixtures" / "simple_suite"


def test_code_under_test_validator_accepts_multiple_modules(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "load_stratum_manifest",
        lambda _stratum: {
            "subtests": {
                "test_many.py": {
                    "code_under_test": [
                        {"module": "rack.cli"},
                        {"module": "missing.module"},
                    ]
                }
            }
        },
    )
    monkeypatch.setattr(
        cli,
        "resolve_module_to_path",
        lambda module: Path("cli.py") if module == "rack.cli" else None,
    )

    errors = cli.validate_code_under_test("L1_self_hosting")

    assert len(errors) == 1
    assert "missing.module" in errors[0]


def test_code_under_test_validator_rejects_malformed_entries(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "load_stratum_manifest",
        lambda _stratum: {
            "subtests": {
                "test_scalar.py": {"code_under_test": "rack.cli"},
                "test_mixed.py": {"code_under_test": [{"module": "rack.cli"}, "rack.cli"]},
            }
        },
    )
    monkeypatch.setattr(cli, "resolve_module_to_path", lambda _module: Path("cli.py"))

    errors = cli.validate_code_under_test("L1_self_hosting")

    assert errors == [
        "[L1_self_hosting] test_scalar.py: Invalid code_under_test entry",
        "[L1_self_hosting] test_mixed.py: Invalid code_under_test entry",
    ]


def test_code_coverage_map_includes_each_declared_module(monkeypatch) -> None:
    monkeypatch.setattr(cli, "get_strata", lambda: ["L1_self_hosting"])
    monkeypatch.setattr(
        cli,
        "load_stratum_manifest",
        lambda _stratum: {
            "subtests": {
                "test_many.py": {
                    "name": "Many modules",
                    "code_under_test": [
                        {"module": "example.first", "classes": ["First"]},
                        {"module": "example.second", "functions": ["second"]},
                    ],
                }
            }
        },
    )

    coverage = cli.get_code_coverage_map()["by_module"]

    assert set(coverage) == {"example.first", "example.second"}
    assert "First" in coverage["example.first"]["classes"]
    assert "second" in coverage["example.second"]["functions"]


def test_module_resolver_accepts_package_initializer(tmp_path: Path, monkeypatch) -> None:
    package = tmp_path / "src" / "py" / "example" / "nested" / "__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("", encoding="utf-8")
    monkeypatch.setattr(cli, "SOURCE_DIR", tmp_path)

    assert cli.resolve_module_to_path("example.nested") == package


def test_rack_runs_fixture_suite_and_writes_results() -> None:
    suite = SOURCE_SUITE
    results = suite / "rack_results"
    if results.exists():
        shutil.rmtree(results)

    env = os.environ.copy()
    env["RACK_TESTS_DIR"] = str(suite)

    result = subprocess.run(
        [sys.executable, "-m", "rack", "run", "--all", "--progress"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RACK_PROGRESS\ttest_L0_001_smoke" in result.stderr

    summary_path = suite / "rack_results" / "summary.json"
    report_path = suite / "rack_results" / "report.html"
    assert summary_path.exists()
    assert report_path.exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["summary"]["subtests_passed"] == 1
    assert summary["summary"]["tests_passed"] == 1

    progress_files = list((suite / "rack_results" / "progress").glob("*.jsonl"))
    assert len(progress_files) == 1
    progress_events = [
        json.loads(line)
        for line in progress_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in progress_events] == ["start", "progress", "finish"]
    assert progress_events[0]["schema"] == "rack.progress.v0"
    assert progress_events[1]["dut_id"] == "smoke"
