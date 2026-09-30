import json
from pathlib import Path

import pytest
from suite_support import durations

RACK = {
    "id": "L0_006",
    "title": "Duration round trip",
    "purpose": {
        "checks": "Formatting seconds and parsing the text back returns the original seconds.",
        "because": "Saved schedules are reloaded from formatted text, so a lossy pair corrupts them.",
    },
    "concerns": ["fixture"],
    "resources": ["vectors/L0_003_format_duration.json"],
    "implementations": {
        "python": {"status": "implemented"},
        "shadow": {"status": "implemented"},
    },
}

VECTORS = Path(__file__).parent / "vectors" / "L0_003_format_duration.json"
CASES = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
IMPLEMENTATIONS = {
    "python": durations.parse_duration,
    "shadow": durations.parse_duration_shadow,
}


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_duration_round_trip(case, implementation):
    seconds = case["inputs"]["seconds"]
    assert IMPLEMENTATIONS[implementation](durations.format_duration(seconds)) == seconds
