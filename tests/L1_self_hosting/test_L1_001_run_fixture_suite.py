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
        [sys.executable, "-m", "rack", "run", "--all", "--progress"],
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

    progress_files = list((suite / "rack_results" / "progress").glob("*.jsonl"))
    assert len(progress_files) == 1
    progress_events = [
        json.loads(line)
        for line in progress_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in progress_events] == ["start", "progress", "finish"]
    assert progress_events[0]["schema"] == "rack.progress.v0"
    assert progress_events[1]["dut_id"] == "smoke"
