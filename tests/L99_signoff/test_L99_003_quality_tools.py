from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run_command(command: Sequence[str]) -> None:
    result = subprocess.run(
        list(command),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ruff_check_passes() -> None:
    run_command(("ruff", "check", "src", "tests"))


def test_pyright_passes() -> None:
    run_command(("pyright",))


def test_uv_lock_is_current() -> None:
    run_command(("uv", "lock", "--check"))
