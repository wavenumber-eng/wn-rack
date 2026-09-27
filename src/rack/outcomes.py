"""Comparison results and per-row outcomes for self-declared tests.

A comparator returns a list of ``Difference`` records with stable field paths.
``classify`` turns that list, and any declared deferral for the row, into one
Rack outcome. A deferral matches only when the observed differences equal the
declared ones exactly, values included.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from collections.abc import Mapping, Sequence

from rack.declarations import Difference

PASS = "pass"
FAIL = "fail"
DEFERRED = "deferred"
PLANNED = "planned"
SUSPENDED = "suspended"
NOT_APPLICABLE = "not_applicable"
SKIPPED = "skipped"
ERROR = "error"
OUTCOMES = (PASS, FAIL, DEFERRED, PLANNED, SUSPENDED, NOT_APPLICABLE, SKIPPED, ERROR)


def compare_exact(expected: object, actual: object) -> list[Difference]:
    """Structural equality with typed differences; int and float are distinct."""
    differences: list[Difference] = []
    _compare(expected, actual, (), differences)
    return differences


def classify(
    differences: Sequence[Difference], deferred: Sequence[Difference] | None
) -> tuple[str, str]:
    """Return (outcome, detail) for one row."""
    if not deferred:
        if not differences:
            return PASS, ""
        return FAIL, f"{len(differences)} difference(s)"
    if not differences:
        return FAIL, "deferred case now passes; remove its deferral"
    if list(differences) == list(deferred):
        return DEFERRED, f"{len(differences)} declared difference(s)"
    return FAIL, "differences do not match the declared deferral"


def canonical_digest(value: object) -> str:
    """SHA-256 of a canonical JSON encoding; bytes encode as base64 objects."""
    encoded = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def difference_to_dict(difference: Difference) -> dict[str, object]:
    return {
        "path": list(difference.path),
        "kind": difference.kind,
        "expected": _canonical(difference.expected),
        "actual": _canonical(difference.actual),
    }


def _compare(
    expected: object, actual: object, path: tuple[object, ...], out: list[Difference]
) -> None:
    if _nonfinite(actual) and not _nonfinite(expected):
        out.append(Difference(path, "nonfinite", expected, actual))
        return
    if type(expected) is not type(actual):
        out.append(Difference(path, "type", _type_name(expected), _type_name(actual)))
        return
    if isinstance(expected, Mapping) and isinstance(actual, Mapping):
        _compare_mappings(expected, actual, path, out)
    elif isinstance(expected, list) and isinstance(actual, list):
        _compare_lists(expected, actual, path, out)
    elif not _equal_scalars(expected, actual):
        out.append(Difference(path, "value", expected, actual))


def _compare_mappings(
    expected: Mapping[object, object],
    actual: Mapping[object, object],
    path: tuple[object, ...],
    out: list[Difference],
) -> None:
    for key in expected:
        if key not in actual:
            out.append(Difference((*path, key), "missing", expected[key], None))
        else:
            _compare(expected[key], actual[key], (*path, key), out)
    for key in actual:
        if key not in expected:
            out.append(Difference((*path, key), "extra", None, actual[key]))


def _compare_lists(
    expected: list[object], actual: list[object], path: tuple[object, ...], out: list[Difference]
) -> None:
    if len(expected) != len(actual):
        out.append(Difference(path, "length", len(expected), len(actual)))
    for index, (left, right) in enumerate(zip(expected, actual)):
        _compare(left, right, (*path, index), out)


def _equal_scalars(expected: object, actual: object) -> bool:
    if isinstance(expected, float) and isinstance(actual, float):
        if math.isnan(expected) and math.isnan(actual):
            return True
    return expected == actual


def _nonfinite(value: object) -> bool:
    return isinstance(value, float) and not math.isfinite(value)


def _type_name(value: object) -> str:
    return type(value).__name__


def _canonical(value: object) -> object:
    if isinstance(value, (bytes, bytearray)):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value
