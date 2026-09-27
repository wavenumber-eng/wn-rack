"""Parallel stratum runs through pytest-xdist.

A stratum opts in with ``parallel = true`` in ``STRATUM.toml``. Rack then runs
it with ``--dist loadfile``: each test file stays on one worker, so module
fixtures, per-worker adapters, and budget baselines see a whole file. An
implementation that needs an exclusive resource declares ``parallel = false``
under ``[implementations.<name>]`` in ``rack.toml``, and strata run serially
while it is selected.
"""

from __future__ import annotations

from collections.abc import Mapping


def xdist_arguments(
    jobs: int,
    stratum_config: Mapping[str, object],
    rack_config: Mapping[str, object],
    selected: str = "",
) -> tuple[str, str]:
    """Return (extra pytest arguments, note explaining a serial fallback)."""
    if jobs <= 1:
        return "", ""
    if stratum_config.get("parallel") is not True:
        return "", "stratum does not set parallel = true; running serially"
    serial = _serial_implementations(rack_config, selected)
    if serial:
        return "", f"implementations {', '.join(serial)} set parallel = false; running serially"
    return f" -n {jobs} --dist loadfile", ""


def _serial_implementations(rack_config: Mapping[str, object], selected: str) -> list[str]:
    table = rack_config.get("implementations")
    if not isinstance(table, Mapping):
        return []
    names = {name.strip() for name in selected.split(",") if name.strip()}
    return sorted(
        str(name)
        for name, entry in table.items()
        if isinstance(entry, Mapping)
        and entry.get("parallel") is False
        and (not names or name in names)
    )
