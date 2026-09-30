import json
from pathlib import Path

import pytest
from suite_support import durations

RACK = {
    "id": "L0_001",
    "title": "Duration parsing",
    "purpose": {
        "checks": "Parsing duration strings returns the whole seconds they spell.",
        "because": "Callers schedule work from these seconds; a wrong parse shifts every deadline.",
    },
    "concerns": ["fixture"],
    "resources": ["vectors/L0_001_parse_duration.json"],
    "implementations": {
        "python": {"status": "implemented"},
        "shadow": {"status": "implemented"},
        "rust": {"status": "planned", "reason": "not ported yet", "issue": "#1"},
        "cpp": {"status": "suspended", "reason": "port paused by policy"},
        "wasm": {"status": "not_applicable", "reason": "no WASM target"},
    },
}

VECTORS = Path(__file__).parent / "vectors" / "L0_001_parse_duration.json"
CASES = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
# Cases an implementation is known to fail, with the issue that tracks each.
KNOWN_FAILURES = {("fractional_hours", "shadow"): "#2: shadow truncates fractional amounts"}


def unported(text):
    raise NotImplementedError("no port")


IMPLEMENTATIONS = {
    "python": durations.parse_duration,
    "shadow": durations.parse_duration_shadow,
    "rust": unported,
    "cpp": unported,
}


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_duration_parsing(case, implementation, request):
    known = KNOWN_FAILURES.get((case["id"], implementation))
    if known:
        request.applymarker(pytest.mark.xfail(strict=True, reason=known))
    assert IMPLEMENTATIONS[implementation](case["inputs"]["text"]) == case["expect"]["seconds"]
