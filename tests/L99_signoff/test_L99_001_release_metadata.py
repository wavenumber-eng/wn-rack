from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import cast

from rack import __version__


ROOT = Path(__file__).resolve().parents[2]


def load_pyproject() -> Mapping[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return cast(Mapping[str, object], tomllib.load(handle))


def test_pyproject_version_matches_package_version() -> None:
    project = cast(Mapping[str, object], load_pyproject()["project"])
    assert project["version"] == __version__


def test_version_is_date_based_pep440_shape() -> None:
    assert re.fullmatch(r"20\d{2}\.\d{1,2}\.\d{1,2}(?:\.\d+)?", __version__)
    year_text, month_text, day_text, *_patch = __version__.split(".")
    date(int(year_text), int(month_text), int(day_text))


def test_changelog_mentions_current_version() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {__version__}" in changelog


def test_release_notes_match_version_date() -> None:
    year_text, month_text, day_text, *_patch = __version__.split(".")
    release_date = f"{int(year_text):04d}-{int(month_text):02d}-{int(day_text):02d}"
    release_notes = ROOT / "docs" / "releases" / f"{release_date}.md"
    assert release_notes.exists()
    text = release_notes.read_text(encoding="utf-8")
    assert f"`{__version__}`" in text


def test_release_workflow_uses_trusted_publishing() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "release:" in workflow
    assert "types: [published]" in workflow
    assert "id-token: write" in workflow
    assert "pypa/gh-action-pypi-publish" in workflow
