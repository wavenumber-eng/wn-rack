from __future__ import annotations

import json

from rack import RackOutput, clear_current_output, get_current_output, set_current_output


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
