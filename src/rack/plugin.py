"""pytest plugin that applies RACK headers to self-declared test files.

A self-declared file is an ordinary pytest test with a ``RACK`` header. Rack
never runs the test body itself: it decides from the header which rows run or
skip (see ``rack.status_rows``), adds one row per native test the header lists
(see ``rack.native_tests``), and records each row's outcome for reporting.
"""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Generator
from pathlib import Path

import pytest

from rack.code_refs import project_root
from rack.declarations import ImplementationStatus, TestDeclaration, is_self_declared
from rack import native_tests, status_rows

_ROOTS_KEY = pytest.StashKey[dict[Path, Path]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("rack")
    group.addoption(
        "--rack-impl",
        default=None,
        help="Comma-separated implementations to run for self-declared tests",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "rack_implementation(name): implementation a self-declared row runs"
    )
    config.stash[_ROOTS_KEY] = {}


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    status = item.stash.get(status_rows.ROW_KEY, None)
    if status is not None:
        _report_status_row(item, status, report)
    return report


def _report_status_row(
    item: pytest.Item, status: ImplementationStatus, report: pytest.TestReport
) -> None:
    row = status_rows.declared_row(item)
    settled = status_rows.settle(report, status)
    if row is not None and isinstance(row[0], TestDeclaration) and settled is not None:
        status_rows.record(item, row[0], status, *settled)
        # pytest copied the item's properties into this report before this hook
        # ran; the teardown report is the one reports read, so refresh it.
        report.user_properties = list(item.user_properties)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Run or skip rows by their RACK status (see rack.status_rows).

    Native tests the headers list (see rack.native_tests) are added as rows first.
    """
    items.extend(_native_rows(config, items))
    markers = _registered_markers(config)
    selected = _selected_implementations(config)
    for item in items:
        row = status_rows.declared_row(item)
        if row is None:
            continue
        declaration, name = row
        blocked = status_rows.block_reason(declaration, name)
        if blocked is not None or isinstance(declaration, str):
            item.stash[status_rows.BLOCKED_KEY] = blocked or ""
            continue
        status = status_rows.row_status(declaration, name)
        assert status is not None
        item.stash[status_rows.ROW_KEY] = status
        item.add_marker(pytest.mark.rack_implementation(name))
        if name in markers:
            item.add_marker(getattr(pytest.mark, name))
        status_rows.record(item, declaration, status, "")
        reason = status_rows.skip_reason(status, selected)
        if reason is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


def _native_rows(config: pytest.Config, items: list[pytest.Item]) -> list[pytest.Item]:
    added: list[pytest.Item] = []
    seen: set[Path] = set()
    for item in items:
        row = status_rows.declared_row(item)
        module = item.getparent(pytest.Module)
        if row is None or module is None or module.path in seen:
            continue
        seen.add(module.path)
        declaration = row[0]
        if isinstance(declaration, TestDeclaration):
            root = _project_root_for(config, module.path)
            added.extend(native_tests.native_items(module, declaration, root))
    return added


def pytest_pycollect_makemodule(
    module_path: Path, parent: pytest.Collector
) -> pytest.Module | None:
    # A self-declared test may import suite code from the suite root, so the
    # root is on sys.path before pytest imports the module; pytest collects it.
    if module_path.name.startswith("test_") and is_self_declared(module_path):
        _project_root_for(parent.config, module_path)
    return None


def pytest_runtest_setup(item: pytest.Item) -> None:
    blocked = item.stash.get(status_rows.BLOCKED_KEY, None)
    if blocked is not None:
        pytest.fail(blocked)


def _selected_implementations(config: pytest.Config) -> set[str] | None:
    raw = config.getoption("rack_impl") or os.environ.get("RACK_IMPL", "")
    names = {name.strip() for name in str(raw).split(",") if name.strip()}
    return names or None


def _project_root_for(config: pytest.Config, path: Path) -> Path:
    """The project root native test paths are relative to, from the nearest rack.toml."""
    manifest = _find_rack_toml(path)
    roots = config.stash[_ROOTS_KEY]
    if manifest not in roots:
        with manifest.open("rb") as handle:
            roots[manifest] = project_root(manifest.parent, tomllib.load(handle))
        # Test modules import suite code from the suite root.
        if str(manifest.parent) not in sys.path:
            sys.path.append(str(manifest.parent))
    return roots[manifest]


def _find_rack_toml(path: Path) -> Path:
    for directory in path.resolve().parents:
        candidate = directory / "rack.toml"
        if candidate.is_file():
            return candidate
    raise LookupError(f"{path.name}: no rack.toml above this self-declared test")


def _registered_markers(config: pytest.Config) -> set[str]:
    return {
        str(line).split(":", 1)[0].split("(", 1)[0].strip() for line in config.getini("markers")
    }
