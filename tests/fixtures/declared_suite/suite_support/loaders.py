"""Loaders for captured reference results."""

from __future__ import annotations

import json
from pathlib import Path

_REFERENCE = Path(__file__).resolve().parents[1] / "L0_units" / "reference"


def same_seconds(case: object) -> object:
    """A round trip returns the seconds the case started with."""
    inputs = getattr(case, "inputs")
    return {"seconds": inputs["seconds"]}


def read_reference(case: object) -> object:
    case_id = getattr(case, "id")
    return json.loads((_REFERENCE / f"{case_id}.json").read_text(encoding="utf-8"))
