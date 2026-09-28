from suite_support.operations import FormatDuration

RACK = {
    "id": "L0_003",
    "title": "Duration formatting",
    "purpose": {
        "checks": "Formatting seconds matches the captured reference text.",
        "because": "Reports show this text to people, who read a wrong unit as a wrong schedule.",
    },
    "concerns": ["fixture"],
    "operations": ["FormatDuration"],
    "cases": {"file": "vectors/L0_003_format_duration.json"},
    "observation": "FormattedDuration",
    "expect": {"source": "authority", "loader": "fixture.reference", "comparator": "exact"},
    "implementations": {
        "python": {"status": "implemented"},
        "shadow": {"status": "implemented"},
    },
}


def run(case, impl):
    (result,) = impl.batch([FormatDuration(seconds=case.inputs["seconds"])])
    (case.workdir / "result.txt").write_text(result["text"], encoding="utf-8")
    return result
