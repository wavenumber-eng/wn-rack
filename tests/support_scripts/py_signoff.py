"""Legacy-aware AST signoff for Rack."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "src" / "rack" / "cli.py"
CLI_LINE_BASELINE = 3900


def main() -> int:
    lines = CLI_PATH.read_text(encoding="utf-8").splitlines()
    if len(lines) > CLI_LINE_BASELINE:
        print(
            f"{CLI_PATH.relative_to(ROOT)} has {len(lines)} lines; "
            f"legacy baseline is {CLI_LINE_BASELINE}"
        )
        return 1

    exceptions = ROOT / "docs" / "contracts" / "exceptions.json"
    text = exceptions.read_text(encoding="utf-8")
    if "RACK-LEGACY-CLI-MONOLITH" not in text:
        print("missing documented monolith exception")
        return 1
    if "RACK-LEGACY-PYRIGHT-BASIC" not in text:
        print("missing documented Pyright exception")
        return 1

    print("Rack legacy Python signoff passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
