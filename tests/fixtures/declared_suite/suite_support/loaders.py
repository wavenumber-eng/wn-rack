"""Loaders for captured reference results."""

from __future__ import annotations

import json
from pathlib import Path

_REFERENCE = Path(__file__).resolve().parents[1] / "L0_units" / "reference"


def read_reference(case: object) -> object:
    case_id = getattr(case, "id")
    return json.loads((_REFERENCE / f"{case_id}.json").read_text(encoding="utf-8"))
