from __future__ import annotations

import json

from rack import (
    ProgressReporter,
    RackOutput,
    clear_current_output,
    get_current_output,
    set_current_output,
)


def test_rack_output_collects_json_ready_data() -> None:
    output = RackOutput(test_id="L0_001::test_example", test_file="test_example.py")
    output.add_metric("records", 3)
    output.add_timing("parse_ms", 1.25)
    output.add_comparison("hash", "abc", "abc")
    output.add_attachment("note.txt", "hello")
    output.add_tag("synthetic")

    payload = output.to_dict()
    assert payload["metrics"] == {"records": 3}
    assert payload["timings"] == {"parse_ms": 1.25}
    assert payload["attachments"] == ["note.txt"]
    json.loads(output.to_json())


def test_thread_local_output_helpers_round_trip() -> None:
    clear_current_output()
    output = RackOutput(test_id="manual")
    set_current_output(output)
    assert get_current_output() is output
    clear_current_output()
    assert get_current_output() is not output


def test_progress_reporter_writes_jsonl(tmp_path) -> None:
    progress_file = tmp_path / "progress.jsonl"
    reporter = ProgressReporter(
        test_id="test_progress",
        total=2,
        dut_kind="Fixture",
        progress_file=progress_file,
        run_id="run-123",
        enabled=True,
        stderr=False,
        throttle_seconds=0.0,
    )

    reporter.start(worker_count=1)
    reporter.progress(done=1, dut_id="one", metrics={"parse_s": 0.1})
    reporter.finish(done=2, failures=0)

    events = [
        json.loads(line)
        for line in progress_file.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == ["start", "progress", "finish"]
    assert events[0]["schema"] == "rack.progress.v0"
    assert events[1]["run_id"] == "run-123"
    assert events[1]["done"] == 1
    assert events[1]["total"] == 2
    assert events[1]["dut_kind"] == "Fixture"
    assert events[1]["dut_id"] == "one"
    assert events[1]["metrics"] == {"parse_s": 0.1}
