from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUITE = ROOT / "tests" / "fixtures" / "simple_suite"


def test_rack_runs_fixture_suite_and_writes_results() -> None:
    suite = SOURCE_SUITE
    results = suite / "rack_results"
    if results.exists():
        shutil.rmtree(results)

    env = os.environ.copy()
    env["RACK_TESTS_DIR"] = str(suite)

    result = subprocess.run(
        [sys.executable, "-m", "rack", "run", "--all"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary_path = suite / "rack_results" / "summary.json"
    report_path = suite / "rack_results" / "report.html"
    assert summary_path.exists()
    assert report_path.exists()

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["summary"]["subtests_passed"] == 1
    assert summary["summary"]["tests_passed"] == 1
