"""Operations the fixture tests send through ``impl.batch``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParseDuration:
    text: str


@dataclass(frozen=True)
class FormatDuration:
    seconds: int
