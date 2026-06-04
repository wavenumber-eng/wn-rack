from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rack", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_global_version_reports_dependency_versions() -> None:
    result = run_cli("--version")
    assert result.returncode == 0
    assert "wn-rack 2026.6.4" in result.stdout
    assert "python " in result.stdout
    assert "pytest " in result.stdout


def test_cli_version_command_reports_same_version() -> None:
    result = run_cli("version")
    assert result.returncode == 0
    assert "wn-rack 2026.6.4" in result.stdout


def test_cli_help_lists_public_commands() -> None:
    result = run_cli("--help")
    assert result.returncode == 0
    for command in ("inventory", "list", "new", "refresh", "report", "run", "status", "version"):
        assert command in result.stdout


def test_cli_command_help_starts_for_public_commands() -> None:
    for command in ("inventory", "list", "new", "refresh", "report", "run", "status", "version"):
        result = run_cli(command, "--help")
        assert result.returncode == 0
        assert command in result.stdout
