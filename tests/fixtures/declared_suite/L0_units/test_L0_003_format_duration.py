"""format_duration renders seconds the way the captured reference does."""

RACK = {
    "id": "L0_003",
    "title": "Duration formatting",
    "concerns": ["fixture"],
    "cases": {"file": "vectors/L0_003_format_duration.json"},
    "observation": "FormattedDuration",
    "expect": {"source": "authority", "loader": "fixture.reference", "comparator": "exact"},
    "implementations": {"python": "implemented", "shadow": "implemented"},
}


def run(case, impl):
    (result,) = impl.batch([{"op": "format_duration", "seconds": case.inputs["seconds"]}])
    (case.workdir / "result.txt").write_text(result["text"], encoding="utf-8")
    return result
