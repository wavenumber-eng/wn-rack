"""CLI adapter for ``rack parity``."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from rack.parity import build_parity, format_parity_text


def cmd_parity(args, tests_dir: Path, results_dir: Path, strata: Sequence[str]) -> int:
    """Print parity accounting from declarations and the latest results."""
    selected = [args.stratum] if args.stratum else list(strata)
    unknown = [name for name in selected if name not in strata]
    if unknown:
        print(f"Unknown stratum: {', '.join(unknown)}")
        return 1
    report = build_parity(tests_dir, results_dir, selected, group_by=args.by)
    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        print(format_parity_text(report))
    return 0
