"""Rack rows recorded by self-declared tests.

The plugin stores each row's Rack outcome in pytest ``user_properties``; the
JSON report carries them to ``rack run``, ``rack refresh``, and the report.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def rack_row(test: Mapping[str, object]) -> dict[str, object] | None:
    """Return the Rack row one pytest-json-report test entry recorded, if any."""
    properties: dict[str, object] = {}
    items = test.get("user_properties")
    if isinstance(items, Sequence):
        for item in items:
            if isinstance(item, Mapping):
                properties.update({str(key): value for key, value in item.items()})
    if "rack_test" not in properties:
        return None
    return {
        "test": properties.get("rack_test", ""),
        "case": properties.get("rack_case", ""),
        "implementation": properties.get("rack_implementation", ""),
        "status": properties.get("rack_status", ""),
        "outcome": properties.get("rack_outcome", ""),
        "detail": properties.get("rack_detail", ""),
        "differences": properties.get("rack_differences", []),
    }
