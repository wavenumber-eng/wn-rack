from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rack.parallel import xdist_arguments
from rack.progress import worker_progress_file
from rack.progress_cli import ProgressTailer

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUITE = ROOT / "tests" / "fixtures" / "declared_suite"

RACK_CONFIG = {
    "implementations": {
        "python": {"adapter": "a:A"},
        "altium": {"adapter": "a:B", "parallel": False},
    }
}


@pytest.mark.parametrize(
    ("jobs", "stratum", "selected", "expected_args", "note"),
    [
        (1, {"parallel": True}, "", "", ""),
        (4, {}, "", "", "does not set parallel"),
        (4, {"parallel": True}, "", "", "altium set parallel = false"),
        (4, {"parallel": True}, "python", " -n 4 --dist loadfile", ""),
        (4, {"parallel": True}, "python,altium", "", "altium"),
    ],
)
def test_xdist_arguments(
    jobs: int, stratum: dict[str, object], selected: str, expected_args: str, note: str
) -> None:
    args, reason = xdist_arguments(jobs, stratum, RACK_CONFIG, selected)

    assert args == expected_args
    assert note in reason and bool(reason) == bool(note)


def test_workers_write_their_own_progress_files_and_the_tailer_merges_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = tmp_path / "run.jsonl"
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    assert worker_progress_file(base) == base
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    assert worker_progress_file(base) == tmp_path / "run.gw1.jsonl"

    event = {"schema": "rack.progress.v0", "event": "start", "test_id": "t", "elapsed_s": 0.0}
    for path in (base, tmp_path / "run.gw0.jsonl", tmp_path / "run.gw1.jsonl"):
        path.write_text(json.dumps({**event, "test_id": path.stem}) + "\n", encoding="utf-8")

    tailer = ProgressTailer(base)
    tailer.drain()
    tailer.drain()

    err = capsys.readouterr().err
    assert [line.split("\t")[1] for line in err.splitlines()] == ["run", "run.gw0", "run.gw1"]


def test_rack_run_with_jobs_keeps_row_outcomes() -> None:
    results = SOURCE_SUITE / "rack_results"
    if results.exists():
        shutil.rmtree(results)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("RACK_LANE", "WN_RACK_LANE", "WN_TEST_LANE", "RACK_IMPL")
    }
    env["RACK_TESTS_DIR"] = str(SOURCE_SUITE)

    run = subprocess.run(
        [sys.executable, "-m", "rack", "run", "--all", "--jobs", "2"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    subtest = json.loads(
        (results / "subtests" / "test_L0_001_parse_duration.json").read_text(encoding="utf-8")
    )
    rows = {test["name"]: test["rack"]["outcome"] for test in subtest["tests"]}
    assert rows["L0_001[hours_and_minutes-python]"] == "pass"
    assert rows["L0_001[fractional_hours-shadow]"] == "deferred"
    assert rows["L0_001[hours_and_minutes-rust]"] == "planned"
    assert "created: 2/2 workers" in run.stdout
