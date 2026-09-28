"""Run or skip the rows of a plain pytest test by the statuses in its RACK header.

A pytest-form test parametrizes over ``implementation``. For each row Rack
looks the implementation up in the header: implemented rows run; planned and
suspended rows are skipped with their reason unless ``--rack-impl`` selects
them, and a selected one that fails is reported without failing the run;
not-applicable rows are skipped. A row for an implementation the header does
not declare fails. Rack never changes what the test body does.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from rack.declarations import (
    DeclarationError,
    ImplementationStatus,
    TestDeclaration,
    is_self_declared,
    load_declaration,
)

ROW_KEY = pytest.StashKey[ImplementationStatus]()
# Why a row cannot run: its header is invalid or does not declare it.
BLOCKED_KEY = pytest.StashKey[str]()
NON_GATING = ("planned", "suspended")


def declared_row(item: pytest.Item) -> tuple[TestDeclaration | str, str] | None:
    """The header (or why it is invalid) and implementation name of a pytest-form row."""
    callspec = getattr(item, "callspec", None)
    implementation = callspec.params.get("implementation") if callspec else None
    if not isinstance(implementation, str):
        return None
    declaration = _pytest_declaration(Path(str(item.path)))
    if declaration is None:
        return None
    return declaration, implementation


def block_reason(declaration: TestDeclaration | str, name: str) -> str | None:
    """Why the row must fail before running, or None."""
    if isinstance(declaration, str):
        return f"invalid RACK header, so statuses cannot be applied: {declaration}"
    if declaration.status_of(name) is None:
        return f"implementation {name!r} is not declared in RACK implementations"
    return None


def skip_reason(status: ImplementationStatus, selected: set[str] | None) -> str | None:
    if selected is not None and status.name not in selected:
        return "skipped: implementation not selected"
    if status.status == "not_applicable":
        return f"not_applicable: {status.reason}"
    if selected is None and status.status in NON_GATING:
        suffix = f" ({status.issue})" if status.issue else ""
        return f"{status.status}: {status.reason}{suffix}"
    return None


def record(
    item: pytest.Item,
    declaration: TestDeclaration,
    status: ImplementationStatus,
    outcome: str,
    detail: str = "",
) -> None:
    callspec = getattr(item, "callspec", None)
    case = callspec.params.get("case") if callspec else None
    case_id = case.get("id", "") if isinstance(case, dict) else ""
    item.user_properties[:] = [
        ("rack_test", declaration.id),
        ("rack_case", str(case_id)),
        ("rack_implementation", status.name),
        ("rack_status", status.status),
        ("rack_outcome", outcome),
        ("rack_detail", detail),
        ("rack_differences", []),
    ]


def settle(report: pytest.TestReport, status: ImplementationStatus) -> tuple[str, str] | None:
    """The row's Rack outcome and detail for one report phase, or None.

    A selected planned or suspended row that fails is turned into an expected
    failure, so it is reported without failing the run.
    """
    if report.when == "setup" and report.skipped:
        detail = _skip_detail(report)
        return _skip_outcome(detail), detail
    if report.when != "call":
        return None
    if report.failed and status.status in NON_GATING:
        detail = (report.longreprtext.splitlines() or [""])[-1]
        report.outcome = "skipped"
        report.wasxfail = f"{status.status} {status.name}: {detail}"
        return status.status, detail
    return ("pass" if report.passed else "fail"), ""


def _skip_detail(report: pytest.TestReport) -> str:
    longrepr = report.longrepr
    detail = str(longrepr[2]) if isinstance(longrepr, tuple) else str(longrepr)
    return detail.removeprefix("Skipped: ")


def _skip_outcome(detail: str) -> str:
    prefix = detail.split(":", 1)[0]
    return prefix if prefix in ("planned", "suspended", "not_applicable") else "skipped"


def _pytest_declaration(path: Path) -> TestDeclaration | str | None:
    if not path.name.startswith("test_") or not path.is_file():
        return None
    stat = path.stat()
    return _cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


@functools.lru_cache(maxsize=4096)
def _cached(path: str, _mtime_ns: int, _size: int) -> TestDeclaration | str | None:
    if not is_self_declared(Path(path)):
        return None
    try:
        declaration = load_declaration(Path(path))
    except DeclarationError as error:
        return str(error)
    return declaration if declaration.form == "pytest" else None
