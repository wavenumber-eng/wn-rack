from suite_support.operations import ParseDuration

RACK = {
    "id": "L0_001",
    "title": "Duration parsing",
    "purpose": {
        "checks": "Parsing duration strings returns the whole seconds they spell.",
        "because": "Callers schedule work from these seconds; a wrong parse shifts every deadline.",
    },
    "concerns": ["fixture"],
    "operations": ["ParseDuration"],
    "cases": {"file": "vectors/L0_001_parse_duration.json"},
    "observation": "DurationResult",
    "expect": {"source": "contract", "comparator": "exact"},
    "implementations": {
        "python": {"status": "implemented"},
        "shadow": {"status": "implemented"},
        "rust": {"status": "planned", "reason": "not ported yet", "issue": "#1"},
        "cpp": {"status": "suspended", "reason": "port paused by policy"},
        "wasm": {"status": "not_applicable", "reason": "no WASM target"},
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
    (result,) = impl.batch([ParseDuration(text=case.inputs["text"])])
    return result
