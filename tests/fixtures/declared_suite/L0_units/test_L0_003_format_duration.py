RACK = {
    "id": "L0_003",
    "title": "Duration formatting",
    "purpose": {
        "checks": "Formatting seconds matches the captured reference text.",
        "because": "Reports show this text to people, who read a wrong unit as a wrong schedule.",
    },
    "concerns": ["fixture"],
    "cases": {"file": "vectors/L0_003_format_duration.json"},
    "observation": "FormattedDuration",
    "expect": {"source": "authority", "loader": "fixture.reference", "comparator": "exact"},
    "implementations": {
        "python": {
            "status": "implemented",
            "code": [
                {
                    "file": "suite_support/durations.py",
                    "module": "suite_support.durations",
                    "function": "format_duration",
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
    (result,) = impl.batch([{"op": "format_duration", "seconds": case.inputs["seconds"]}])
    (case.workdir / "result.txt").write_text(result["text"], encoding="utf-8")
    return result
