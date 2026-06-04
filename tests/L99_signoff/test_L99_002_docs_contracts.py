from __future__ import annotations

import importlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import pytest

from rack.cli import main


ROOT = Path(__file__).resolve().parents[2]


def load_json_mapping(path: Path) -> Mapping[str, object]:
    return cast(Mapping[str, object], json.loads(path.read_text(encoding="utf-8")))


def object_sequence(value: object, key: str) -> Sequence[Mapping[str, object]]:
    if not isinstance(value, list):
        raise TypeError(f"expected {key} to be a list")
    items: list[Mapping[str, object]] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            raise TypeError(f"expected {key} entries to be objects")
        items.append(cast(Mapping[str, object], item))
    return items


def test_command_manifest_matches_help_and_design_doc(capsys) -> None:
    manifest = load_json_mapping(ROOT / "docs" / "contracts" / "command_manifest.v0.json")
    commands = object_sequence(manifest["commands"], "commands")
    design_doc = (ROOT / "docs" / "design" / "cli.html").read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    for command in commands:
        name = cast(str, command["name"])
        assert name in help_text
        assert f'data-command="{name}"' in design_doc


def test_interface_manifest_matches_exports_and_design_doc() -> None:
    manifest = load_json_mapping(ROOT / "docs" / "contracts" / "interface_manifest.v0.json")
    interfaces = object_sequence(manifest["interfaces"], "interfaces")
    design_doc = (ROOT / "docs" / "design" / "public-api.html").read_text(encoding="utf-8")

    for item in interfaces:
        name = cast(str, item["name"])
        module_name, symbol_name = name.rsplit(".", 1)
        module = importlib.import_module(module_name)
        assert hasattr(module, symbol_name)
        assert f'data-interface="{name}"' in design_doc


def test_legacy_exceptions_are_documented() -> None:
    exceptions = load_json_mapping(ROOT / "docs" / "contracts" / "exceptions.json")
    entries = object_sequence(exceptions["exceptions"], "exceptions")
    ids = {cast(str, entry["id"]) for entry in entries}
    assert "RACK-LEGACY-CLI-MONOLITH" in ids
    assert "RACK-LEGACY-PYRIGHT-BASIC" in ids
