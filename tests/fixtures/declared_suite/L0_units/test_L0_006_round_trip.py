from suite_support.operations import FormatDuration, ParseDuration

RACK = {
    "id": "L0_006",
    "title": "Duration round trip",
    "purpose": {
        "checks": "Formatting seconds and parsing the text back returns the original seconds.",
        "because": "Saved schedules are reloaded from formatted text, so a lossy pair corrupts them.",
    },
    "concerns": ["fixture"],
    "operations": ["FormatDuration", "ParseDuration"],
    "cases": {"file": "vectors/L0_003_format_duration.json"},
    "observation": "DurationResult",
    "expect": {"source": "property", "loader": "fixture.same_seconds", "comparator": "exact"},
    "implementations": {
        "python": {"status": "implemented"},
        "shadow": {"status": "implemented"},
    },
}


def run(case, impl):
    (formatted,) = impl.batch([FormatDuration(seconds=case.inputs["seconds"])])
    (parsed,) = impl.batch([ParseDuration(text=formatted["text"])])
    return parsed
