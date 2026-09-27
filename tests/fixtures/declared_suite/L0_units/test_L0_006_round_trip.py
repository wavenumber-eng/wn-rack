"""Formatting then parsing a duration returns the original seconds."""

RACK = {
    "id": "L0_006",
    "title": "Duration round trip",
    "concerns": ["fixture"],
    "cases": {"file": "vectors/L0_003_format_duration.json"},
    "observation": "DurationResult",
    "expect": {"source": "property", "loader": "fixture.same_seconds", "comparator": "exact"},
    "implementations": {"python": "implemented", "shadow": "implemented"},
}


def run(case, impl):
    (formatted,) = impl.batch([{"op": "format_duration", "seconds": case.inputs["seconds"]}])
    (parsed,) = impl.batch([{"op": "parse_duration", "text": formatted["text"]}])
    return parsed
