RACK = {
    "id": "L0_006",
    "title": "Duration round trip",
    "purpose": {
        "checks": "Formatting seconds and parsing the text back returns the original seconds.",
        "because": "Saved schedules are reloaded from formatted text, so a lossy pair corrupts them.",
    },
    "concerns": ["fixture"],
    "cases": {"file": "vectors/L0_003_format_duration.json"},
    "observation": "DurationResult",
    "expect": {"source": "property", "loader": "fixture.same_seconds", "comparator": "exact"},
    "implementations": {
        "python": {
            "status": "implemented",
            "code": [
                {
                    "file": "suite_support/durations.py",
                    "module": "suite_support.durations",
                    "function": "format_duration",
                },
                {
                    "file": "suite_support/durations.py",
                    "module": "suite_support.durations",
                    "function": "parse_duration",
                },
            ],
        },
        "shadow": {
            "status": "implemented",
            "code": [
                {
                    "file": "suite_support/durations.py",
                    "module": "suite_support.durations",
                    "function": "format_duration",
                },
            ],
        },
    },
}


def run(case, impl):
    (formatted,) = impl.batch([{"op": "format_duration", "seconds": case.inputs["seconds"]}])
    (parsed,) = impl.batch([{"op": "parse_duration", "text": formatted["text"]}])
    return parsed
