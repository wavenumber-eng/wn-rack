"""Adapters that run fixture operations against one implementation each."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from suite_support import durations


class _Adapter:
    def __init__(self, parse: Callable[[str], int]) -> None:
        self._parse = parse
        self.closed = False

    def batch(self, operations: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        return [self._run(operation) for operation in operations]

    def close(self) -> None:
        self.closed = True

    def _run(self, operation: Mapping[str, object]) -> dict[str, object]:
        if operation["op"] == "parse_duration":
            return {"seconds": self._parse(str(operation["text"]))}
        if operation["op"] == "format_duration":
            return {"text": durations.format_duration(int(str(operation["seconds"])))}
        return {"error": "unsupported_operation"}


class ReferenceAdapter(_Adapter):
    def __init__(self) -> None:
        super().__init__(durations.parse_duration)


class ShadowAdapter(_Adapter):
    def __init__(self) -> None:
        super().__init__(durations.parse_duration_shadow)
