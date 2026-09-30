"""Run or skip the rows of a plain pytest test by the statuses in its RACK header.

A row's implementation is its ``implementation`` parameter, a native test's own
entry, or else the implementation the file's test runs (``TestDeclaration.own``);
a check's rows are ``check`` rows. Implemented rows run; planned and suspended
rows are skipped with their reason unless ``--rack-impl`` selects them, and a
selected one that fails is reported without failing the run; not-applicable
rows are skipped. Every row of a file whose header is invalid errors, and so
does a row for an implementation the header does not declare. Rack never
changes what the test body does.
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
CHECK = "check"


def declared_row(item: pytest.Item) -> tuple[TestDeclaration | str, str] | None:
    """The header (or why it is invalid) and implementation name of a pytest-form row."""
    native = getattr(item, "rack_native_row", None)
    if native is not None:
        return native
    declaration = _pytest_declaration(Path(str(item.path)))
    if declaration is None:
        return None
    if isinstance(declaration, str):
        return declaration, ""
    callspec = getattr(item, "callspec", None)
    implementation = callspec.params.get("implementation") if callspec else None
    if isinstance(implementation, str):
        return declaration, implementation
    if declaration.kind == CHECK:
        return declaration, CHECK
    own = declaration.own
    return (declaration, own.name) if own is not None else None


def row_status(declaration: TestDeclaration, name: str) -> ImplementationStatus | None:
    """The header status of a row; a check's rows always run."""
    if declaration.kind == CHECK:
        return ImplementationStatus(CHECK, "implemented")
    return declaration.status_of(name)


def block_reason(declaration: TestDeclaration | str, name: str) -> str | None:
    """Why the row must fail before running, or None."""
    if isinstance(declaration, str):
        return f"invalid RACK header, so statuses cannot be applied: {declaration}"
    if row_status(declaration, name) is None:
        return f"implementation {name!r} is not declared in RACK implementations"
    return None


def skip_reason(status: ImplementationStatus, selected: set[str] | None) -> str | None:
    # A check tests no implementation, so implementation selection never skips it.
    if status.name == CHECK:
        return None
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
    item.user_properties[:] = [
        ("rack_test", declaration.id),
        ("rack_case", case_name(item)),
        ("rack_implementation", status.name),
        ("rack_status", status.status),
        ("rack_outcome", outcome),
        ("rack_detail", detail),
        ("rack_differences", []),
    ]


def case_name(item: pytest.Item) -> str:
    """The row's case: a ``case`` parameter's id, a native test's file, or the pytest id.

    Tests that parametrize over vector cases name the parameter ``case``, so
    each row is recorded under the vector file's case id.
    """
    native_case = getattr(item, "rack_case", None)
    if isinstance(native_case, str):
        return native_case
    callspec = getattr(item, "callspec", None)
    if callspec is None:
        return ""
    case = callspec.params.get("case")
    if isinstance(case, dict) and isinstance(case.get("id"), str):
        return str(case["id"])
    return str(callspec.id)


def settle(report: pytest.TestReport, status: ImplementationStatus) -> tuple[str, str] | None:
    """The row's Rack outcome and detail for one report phase, or None.

    A selected planned or suspended row that fails is turned into an expected
    failure, so it is reported without failing the run. A known failure the
    test marks with a strict ``xfail`` is a deferred row, with the xfail
    reason as its detail.
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
    if report.skipped and hasattr(report, "wasxfail"):
        return "deferred", str(report.wasxfail).removeprefix("reason: ")
    if report.skipped:
        # The test skipped itself, for example a case outside the active lane.
        detail = _skip_detail(report)
        return _skip_outcome(detail), detail
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
    return declaration
