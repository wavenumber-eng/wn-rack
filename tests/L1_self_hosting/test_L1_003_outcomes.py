from __future__ import annotations

import math

from rack.declarations import Difference
from rack.outcomes import (
    DEFERRED,
    FAIL,
    PASS,
    canonical_digest,
    classify,
    compare_exact,
    difference_to_dict,
)


def test_exact_comparison_reports_typed_differences_with_paths() -> None:
    expected = {"width": 21.5, "items": [1, 2, 3], "name": "U12", "flag": True}
    actual = {"width": 21.25, "items": [1, 5], "label": "U12", "flag": 1}

    differences = compare_exact(expected, actual)

    assert differences == [
        Difference(("width",), "value", 21.5, 21.25),
        Difference(("items",), "length", 3, 2),
        Difference(("items", 1), "value", 2, 5),
        Difference(("name",), "missing", "U12", None),
        Difference(("flag",), "type", "bool", "int"),
        Difference(("label",), "extra", None, "U12"),
    ]


def test_exact_comparison_keeps_int_and_float_distinct_and_flags_nonfinite() -> None:
    assert compare_exact({"n": 5400}, {"n": 5400.0}) == [Difference(("n",), "type", "int", "float")]
    assert compare_exact({"n": 1.0}, {"n": math.inf}) == [
        Difference(("n",), "nonfinite", 1.0, math.inf)
    ]
    assert compare_exact({"n": math.nan}, {"n": math.nan}) == []
    assert compare_exact(b"\x00\x01", b"\x00\x01") == []


def test_classification_without_deferral() -> None:
    assert classify([], None) == (PASS, "")
    outcome, _ = classify([Difference(("a",), "value", 1, 2)], None)
    assert outcome == FAIL


def test_deferral_matches_only_the_exact_declared_differences() -> None:
    declared = [Difference(("seconds",), "value", 5400, 5399)]

    assert classify([Difference(("seconds",), "value", 5400, 5399)], declared)[0] == DEFERRED
    assert classify([Difference(("seconds",), "value", 5400, 5398)], declared)[0] == FAIL
    assert classify(
        [Difference(("seconds",), "value", 5400, 5399), Difference(("x",), "extra", None, 1)],
        declared,
    )[0] == FAIL
    outcome, detail = classify([], declared)
    assert outcome == FAIL and "remove its deferral" in detail


def test_canonical_digest_is_order_independent_and_encodes_bytes() -> None:
    left = {"b": 2, "a": [1, {"blob": b"\x00\x01"}]}
    right = {"a": [1, {"blob": b"\x00\x01"}], "b": 2}

    assert canonical_digest(left) == canonical_digest(right)
    assert canonical_digest(left) != canonical_digest({"b": 2, "a": [1, {"blob": b"\x00"}]})


def test_difference_serializes_for_reports() -> None:
    record = difference_to_dict(Difference(("items", 3, "data"), "value", b"\x01", b"\x02"))

    assert record == {
        "path": ["items", 3, "data"],
        "kind": "value",
        "expected": {"base64": "AQ=="},
        "actual": {"base64": "Ag=="},
    }
