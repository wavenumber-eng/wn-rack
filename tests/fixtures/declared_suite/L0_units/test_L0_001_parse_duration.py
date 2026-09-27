"""parse_duration converts "1h30m" style strings to whole seconds."""

RACK = {
    "id": "L0_001",
    "title": "Duration parsing",
    "concerns": ["fixture"],
    "cases": {"file": "vectors/L0_001_parse_duration.json"},
    "observation": "DurationResult",
    "expect": {"source": "contract", "comparator": "exact"},
    "implementations": {
        "python": "implemented",
        "shadow": "implemented",
        "rust": {"planned": "not ported yet", "issue": "#1"},
        "cpp": {"suspended": "port paused by policy"},
        "wasm": {"not_applicable": "no WASM target"},
    },
    "deferred": {
        "shadow": {
            "fractional_hours": {
                "issue": "#2",
                "differences": [
                    {"path": ["seconds"], "kind": "value", "expected": 5400, "actual": 3600}
                ],
            }
        }
    },
}


def run(case, impl):
    (result,) = impl.batch([{"op": "parse_duration", "text": case.inputs["text"]}])
    return result
