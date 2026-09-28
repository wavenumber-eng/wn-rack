"""Adapters that run fixture operations against one implementation each.

Each adapter's dispatch table maps an operation to its handler, one per line,
so the operation registry can point at the exact line.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from suite_support import durations
from suite_support.operations import FormatDuration, ParseDuration

Handler = Callable[[object], dict[str, object]]


def _parse(operation: ParseDuration) -> dict[str, object]:
    return {"seconds": durations.parse_duration(operation.text)}


def _parse_shadow(operation: ParseDuration) -> dict[str, object]:
    return {"seconds": durations.parse_duration_shadow(operation.text)}


def _format(operation: FormatDuration) -> dict[str, object]:
    return {"text": durations.format_duration(operation.seconds)}


class _Adapter:
    handlers: Mapping[type, Handler] = {}

    def __init__(self) -> None:
        self.closed = False

    def batch(self, operations: Sequence[object]) -> list[dict[str, object]]:
        return [self.handlers[type(operation)](operation) for operation in operations]

    def close(self) -> None:
        self.closed = True


class ReferenceAdapter(_Adapter):
    handlers = {
        ParseDuration: _parse,
        FormatDuration: _format,
    }


class ShadowAdapter(_Adapter):
    handlers = {
        ParseDuration: _parse_shadow,
        FormatDuration: _format,
    }
