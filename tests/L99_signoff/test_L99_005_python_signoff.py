from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_python_hygiene_signoff_passes() -> None:
    result = subprocess.run(
        [sys.executable, "tests/support_scripts/py_signoff.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
