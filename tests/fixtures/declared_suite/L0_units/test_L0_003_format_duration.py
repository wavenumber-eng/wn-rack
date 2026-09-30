import json
from pathlib import Path

import pytest
from suite_support import durations

RACK = {
    "id": "L0_003",
    "title": "Duration formatting",
    "purpose": {
        "checks": "Formatting seconds matches the captured reference text.",
        "because": "Reports show this text to people, who read a wrong unit as a wrong schedule.",
    },
    "concerns": ["fixture"],
    "resources": ["vectors/L0_003_format_duration.json"],
    "implementations": {
        "python": {"status": "implemented"},
        "shadow": {"status": "implemented"},
    },
}

HERE = Path(__file__).parent
CASES = json.loads((HERE / "vectors" / "L0_003_format_duration.json").read_text("utf-8"))["cases"]
IMPLEMENTATIONS = {
    "python": durations.format_duration,
    "shadow": durations.format_duration,
}


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_duration_formatting(case, implementation):
    # The captured reference text for each case, one file per case id.
    reference = json.loads((HERE / "reference" / f"{case['id']}.json").read_text("utf-8"))
    assert IMPLEMENTATIONS[implementation](case["inputs"]["seconds"]) == reference["text"]
