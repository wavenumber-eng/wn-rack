from __future__ import annotations

import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast


ROOT = Path(__file__).resolve().parents[2]


REQUIRED_FILES = (
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "uv.lock",
)


def test_required_hygiene_files_exist() -> None:
    for relative_path in REQUIRED_FILES:
        assert (ROOT / relative_path).exists(), relative_path


def test_env_file_is_ignored_and_not_tracked() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    result = subprocess.run(
        ["git", "ls-files", ".env"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_sdist_excludes_temporary_plans_and_research() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = cast(Mapping[str, object], tomllib.load(handle))
    tool = cast(Mapping[str, object], pyproject["tool"])
    hatch = cast(Mapping[str, object], tool["hatch"])
    build = cast(Mapping[str, object], hatch["build"])
    targets = cast(Mapping[str, object], build["targets"])
    sdist = cast(Mapping[str, object], targets["sdist"])
    exclude = cast(Sequence[str], sdist["exclude"])
    assert "/docs/plans/**" in exclude
    assert "/docs/research/**" in exclude
