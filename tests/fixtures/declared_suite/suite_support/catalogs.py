"""Catalogs that enumerate cases outside a vector file."""

from __future__ import annotations

from pathlib import Path

from rack.declarations import Case

_VECTORS = Path(__file__).resolve().parents[1] / "L0_units" / "vectors"


def vector_files() -> list[Case]:
    return [
        Case(id=path.stem, inputs={"path": str(path)}, expect=None, lane="fast")
        for path in sorted(_VECTORS.glob("*.json"))
    ]
