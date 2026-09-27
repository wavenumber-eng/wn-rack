"""Two small duration implementations; shadow truncates fractional amounts."""

from __future__ import annotations

import re

_UNITS = {"h": 3600, "m": 60, "s": 1}
_TOKEN = re.compile(r"(\d+(?:\.\d+)?)([hms])")


def parse_duration(text: str) -> int:
    return round(sum(float(amount) * _UNITS[unit] for amount, unit in _TOKEN.findall(text)))


def parse_duration_shadow(text: str) -> int:
    return sum(int(float(amount)) * _UNITS[unit] for amount, unit in _TOKEN.findall(text))


def format_duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    parts = [
        f"{value}{unit}" for value, unit in ((hours, "h"), (minutes, "m"), (secs, "s")) if value
    ]
    return "".join(parts) or "0s"
