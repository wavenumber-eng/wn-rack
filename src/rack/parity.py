"""Parity accounting for a Rack suite.

Implementation statuses come from test declarations alone; case outcomes come
from the latest per-file results that ``rack run`` wrote, when they exist.
Legacy test files count as debt until they are converted.
"""

from __future__ import annotations

import json
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rack.audit import audit_suite
from rack.declarations import (
    STATUSES,
    DeclarationError,
    TestDeclaration,
    declared_test_files,
    load_declaration,
    load_vector_file,
)

PARITY_REPORT_TYPE = "rack.parity_report"
PARITY_REPORT_VERSION = "a0"
GROUP_BY = ("stratum", "concern")
ROW_OUTCOMES = ("pass", "fail", "deferred", "error")
DECLARATION_AUDIT_CODES = frozenset(
    {
        "declared_file_in_manifest",
        "duplicate_test_id",
        "invalid_cases",
        "invalid_declaration",
        "test_module_import",
    }
)


@dataclass(frozen=True)
class TestEntry:
    """One test file as parity sees it."""

    stratum: str
    file: str
    id: str
    title: str
    kind: str  # "test", "check", "legacy", or "invalid"
    concerns: tuple[str, ...]
    statuses: Mapping[str, str] = field(default_factory=dict)
    notes: Mapping[str, str] = field(default_factory=dict)  # issue or reason per implementation
    deferred: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    case_count: int | None = None


@dataclass(frozen=True)
class Row:
    """The latest outcome of one (test, case, implementation)."""

    test: str
    case: str
    implementation: str
    status: str
    outcome: str
    detail: str


def collect_entries(tests_dir: Path, strata: Sequence[str]) -> list[TestEntry]:
    """Every test file in the given strata, declared or legacy."""
    entries: list[TestEntry] = []
    for stratum in strata:
        stratum_dir = tests_dir / stratum
        declared = set(declared_test_files(stratum_dir)) if stratum_dir.is_dir() else set()
        legacy_concerns, default = _manifest_concerns(stratum_dir)
        for path in sorted(stratum_dir.glob("test_*.py")):
            if path in declared:
                entries.append(_declared_entry(stratum, path, default))
            else:
                concerns = legacy_concerns.get(path.name, default)
                entries.append(TestEntry(stratum, path.name, path.stem, "", "legacy", concerns))
    return entries


def latest_rows(results_dir: Path, entries: Iterable[TestEntry]) -> list[Row]:
    """Rows from the latest per-file results of the declared tests."""
    rows: list[Row] = []
    for entry in entries:
        if entry.kind not in ("test", "check"):
            continue
        path = results_dir / "subtests" / f"{Path(entry.file).stem}.json"
        if path.is_file():
            rows.extend(_rows_from_result(path))
    return rows


def build_parity(
    tests_dir: Path,
    results_dir: Path,
    strata: Sequence[str],
    *,
    group_by: str = "stratum",
) -> dict[str, object]:
    """The versioned parity report for the given strata."""
    entries = collect_entries(tests_dir, strata)
    rows = latest_rows(results_dir, entries)
    implementations = _implementation_order(entries)
    by_test = _rows_by_test(rows)
    groups = [
        _group_summary(name, members, implementations, by_test)
        for name, members in _grouped(entries, group_by)
    ]
    return {
        "type": PARITY_REPORT_TYPE,
        "version": PARITY_REPORT_VERSION,
        "group_by": group_by,
        "strata": list(strata),
        "implementations": implementations,
        "groups": groups,
        "totals": _group_summary("total", entries, implementations, by_test),
        "tests": [_test_detail(entry, implementations, by_test) for entry in entries],
        "debt": _debt(tests_dir, strata, entries, rows),
    }


def _declared_entry(stratum: str, path: Path, default_concerns: tuple[str, ...]) -> TestEntry:
    try:
        declaration = load_declaration(path)
    except DeclarationError:
        return TestEntry(stratum, path.name, path.stem, "", "invalid", default_concerns)
    return TestEntry(
        stratum=stratum,
        file=path.name,
        id=declaration.id,
        title=declaration.title,
        kind=declaration.kind,
        concerns=declaration.concerns or default_concerns,
        statuses={entry.name: entry.status for entry in declaration.implementations},
        notes={
            entry.name: entry.issue or entry.reason
            for entry in declaration.implementations
            if entry.status != "implemented"
        },
        deferred=declaration.deferred_issues,
        case_count=_case_count(declaration),
    )


def _case_count(declaration: TestDeclaration) -> int | None:
    file_ref = declaration.cases.get("file")
    if not isinstance(file_ref, str):
        return None
    try:
        return len(load_vector_file(declaration.path.parent / file_ref).cases)
    except DeclarationError:
        return None


def _manifest_concerns(stratum_dir: Path) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
    """Legacy per-file concerns and the stratum default from STRATUM.toml."""
    manifest_path = stratum_dir / "STRATUM.toml"
    if not manifest_path.is_file():
        return {}, ()
    try:
        with manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)
    except tomllib.TOMLDecodeError:
        return {}, ()
    default = tuple(str(tag) for tag in manifest.get("concerns", []))
    concerns: dict[str, tuple[str, ...]] = {}
    for subtest in manifest.get("subtests", []):
        if isinstance(subtest, dict) and isinstance(subtest.get("file"), str):
            tags = tuple(str(tag) for tag in subtest.get("concerns", [])) or default
            concerns[subtest["file"]] = tags
    return concerns, default


def _rows_from_result(path: Path) -> list[Row]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows: list[Row] = []
    for test in payload.get("tests", []):
        rack = test.get("rack") if isinstance(test, dict) else None
        if isinstance(rack, dict):
            rows.append(
                Row(
                    test=str(rack.get("test", "")),
                    case=str(rack.get("case", "")),
                    implementation=str(rack.get("implementation", "")),
                    status=str(rack.get("status", "")),
                    outcome=str(rack.get("outcome", "")),
                    detail=str(rack.get("detail", "")),
                )
            )
    return rows


def _implementation_order(entries: Iterable[TestEntry]) -> list[str]:
    names: list[str] = []
    for entry in entries:
        for name in entry.statuses:
            if name not in names:
                names.append(name)
    return names


def _rows_by_test(rows: Iterable[Row]) -> dict[str, list[Row]]:
    grouped: dict[str, list[Row]] = {}
    for row in rows:
        grouped.setdefault(row.test, []).append(row)
    return grouped


def _grouped(entries: Sequence[TestEntry], group_by: str) -> list[tuple[str, list[TestEntry]]]:
    groups: dict[str, list[TestEntry]] = {}
    for entry in entries:
        keys = (entry.stratum,) if group_by == "stratum" else (entry.concerns or ("(none)",))
        for key in keys:
            groups.setdefault(key, []).append(entry)
    return list(groups.items())


def _group_summary(
    name: str,
    entries: Sequence[TestEntry],
    implementations: Sequence[str],
    by_test: Mapping[str, Sequence[Row]],
) -> dict[str, object]:
    kinds = Counter(entry.kind for entry in entries)
    return {
        "name": name,
        "files": len(entries),
        "declared": kinds["test"] + kinds["check"],
        "legacy": kinds["legacy"],
        "invalid": kinds["invalid"],
        "checks": _check_summary([e for e in entries if e.kind == "check"], by_test),
        "implementations": {
            name: _implementation_summary(name, entries, by_test) for name in implementations
        },
    }


def _check_summary(
    checks: Sequence[TestEntry], by_test: Mapping[str, Sequence[Row]]
) -> dict[str, int]:
    outcomes = Counter(row.outcome for entry in checks for row in by_test.get(entry.id, ()))
    return {"files": len(checks), **{outcome: outcomes[outcome] for outcome in ROW_OUTCOMES}}


def _implementation_summary(
    implementation: str, entries: Sequence[TestEntry], by_test: Mapping[str, Sequence[Row]]
) -> dict[str, object]:
    statuses = Counter(entry.statuses.get(implementation) for entry in entries)
    implemented = [e for e in entries if e.statuses.get(implementation) == "implemented"]
    return {
        "tests": {status: statuses[status] for status in STATUSES},
        "cases": _case_counts(implementation, implemented, by_test),
    }


def _case_counts(
    implementation: str, implemented: Sequence[TestEntry], by_test: Mapping[str, Sequence[Row]]
) -> dict[str, object]:
    outcomes = Counter(
        row.outcome
        for entry in implemented
        for row in by_test.get(entry.id, ())
        if row.implementation == implementation
    )
    cases: dict[str, object] = {outcome: outcomes[outcome] for outcome in ROW_OUTCOMES}
    counts = [entry.case_count for entry in implemented]
    # A catalog's case count is known only after it runs, so totals stay unknown.
    if None in counts:
        cases.update(total=None, not_run=None)
        return cases
    total = sum(count for count in counts if count is not None)
    ran = sum(outcomes[outcome] for outcome in ROW_OUTCOMES)
    cases.update(total=total, not_run=max(total - ran, 0))
    return cases


def _test_detail(
    entry: TestEntry, implementations: Sequence[str], by_test: Mapping[str, Sequence[Row]]
) -> dict[str, object]:
    rows = by_test.get(entry.id, ()) if entry.kind in ("test", "check") else ()
    columns = list(implementations) + (["check"] if entry.kind == "check" else [])
    cells: dict[str, object] = {}
    for name in columns:
        own = [row for row in rows if row.implementation == name]
        cells[name] = {
            "status": "implemented" if name == "check" else entry.statuses.get(name, ""),
            "note": entry.notes.get(name, ""),
            "outcomes": dict(Counter(row.outcome for row in own)),
            "cases": {row.case: row.outcome for row in own},
        }
    return {
        "stratum": entry.stratum,
        "file": entry.file,
        "id": entry.id,
        "title": entry.title,
        "kind": entry.kind,
        "concerns": list(entry.concerns),
        "case_count": entry.case_count,
        "cells": cells,
    }


def _debt(
    tests_dir: Path, strata: Sequence[str], entries: Sequence[TestEntry], rows: Sequence[Row]
) -> dict[str, object]:
    by_id = {entry.id: entry for entry in entries}
    return {
        "legacy_files": [f"{e.stratum}/{e.file}" for e in entries if e.kind == "legacy"],
        "deferred": [
            {"test": e.id, "implementation": name, "case": case, "issue": issue}
            for e in entries
            for name, per_case in e.deferred.items()
            for case, issue in per_case.items()
        ],
        "planned": _status_debt(entries, "planned"),
        "suspended": _status_debt(entries, "suspended"),
        "failing": [
            {"test": row.test, "case": row.case, "implementation": row.implementation}
            for row in rows
            if row.outcome in ("fail", "error") and _gates(by_id.get(row.test), row)
        ],
        "audit": _audit_debt(tests_dir, strata),
    }


def _gates(entry: TestEntry | None, row: Row) -> bool:
    if entry is None:
        return False
    return entry.kind == "check" or entry.statuses.get(row.implementation) == "implemented"


def _status_debt(entries: Sequence[TestEntry], status: str) -> list[dict[str, str]]:
    return [
        {"test": entry.id, "implementation": name, "note": entry.notes.get(name, "")}
        for entry in entries
        for name, value in entry.statuses.items()
        if value == status
    ]


def _audit_debt(tests_dir: Path, strata: Sequence[str]) -> list[dict[str, object]]:
    selected = set(strata)
    return [
        failure.to_json_data()
        for failure in audit_suite(tests_dir).failures
        if failure.code in DECLARATION_AUDIT_CODES
        and (failure.stratum in selected or failure.path.split("/", 1)[0] in selected)
    ]


def format_parity_text(report: Mapping[str, object]) -> str:
    """Human-readable parity accounting."""
    lines = [f"RACK PARITY by {report['group_by']}", ""]
    implementations = [str(name) for name in _list(report.get("implementations"))]
    for group in [*_list(report.get("groups")), report.get("totals")]:
        if isinstance(group, Mapping):
            lines.extend(_group_lines(group, implementations))
    lines.extend(_debt_lines(report.get("debt")))
    return "\n".join(lines)


def _group_lines(group: Mapping[str, object], implementations: Sequence[str]) -> list[str]:
    lines = [
        f"{group['name']}: {group['declared']} declared, {group['legacy']} legacy, "
        f"{group['invalid']} invalid"
    ]
    widths = _table_widths(implementations)
    lines.append("  " + _row(["", "tests:", "", "", "", "|", "cases:"], widths))
    lines.append("  " + _row(_HEADER, widths))
    summaries = group.get("implementations")
    for name in implementations:
        summary = summaries.get(name) if isinstance(summaries, Mapping) else None
        if isinstance(summary, Mapping):
            lines.append("  " + _row(_implementation_cells(name, summary), widths))
    checks = group.get("checks")
    if isinstance(checks, Mapping) and checks.get("files"):
        lines.append(
            f"  checks: {checks['files']} file(s), {checks['pass']} pass, "
            f"{checks['fail']} fail, {checks['error']} error"
        )
    lines.append("")
    return lines


_HEADER = [
    "impl",
    "impl",
    "plan",
    "susp",
    "n/a",
    "|",
    "total",
    "pass",
    "fail",
    "defer",
    "error",
    "not-run",
]


def _implementation_cells(name: str, summary: Mapping[str, object]) -> list[str]:
    tests = summary.get("tests")
    cases = summary.get("cases")
    assert isinstance(tests, Mapping) and isinstance(cases, Mapping)
    total = cases.get("total")
    not_run = cases.get("not_run")
    return [
        name,
        str(tests["implemented"]),
        str(tests["planned"]),
        str(tests["suspended"]),
        str(tests["not_applicable"]),
        "|",
        "?" if total is None else str(total),
        str(cases["pass"]),
        str(cases["fail"]),
        str(cases["deferred"]),
        str(cases["error"]),
        "?" if not_run is None else str(not_run),
    ]


def _table_widths(implementations: Sequence[str]) -> list[int]:
    first = max([4, *(len(name) for name in implementations)])
    return [first, 6, 4, 4, 3, 1, 6, 4, 4, 5, 5, 7]


def _row(cells: Sequence[str], widths: Sequence[int]) -> str:
    padded = [cells[0].ljust(widths[0])]
    padded.extend(
        cell.ljust(width) if cell.endswith(":") else cell.rjust(width)
        for cell, width in zip(cells[1:], widths[1:])
    )
    return " ".join(padded).rstrip()


def _debt_lines(debt: object) -> list[str]:
    if not isinstance(debt, Mapping):
        return []
    lines = ["DEBT"]
    lines.append(f"  legacy test files: {len(_list(debt.get('legacy_files')))}")
    lines.append(_counted("deferred cases", debt.get("deferred"), "issue"))
    lines.append(_counted("planned", debt.get("planned"), "note"))
    lines.append(_counted("suspended", debt.get("suspended"), "implementation"))
    lines.append(f"  failing rows: {len(_list(debt.get('failing')))}")
    audit = Counter(
        str(item.get("code")) for item in _list(debt.get("audit")) if isinstance(item, Mapping)
    )
    lines.append(_counts_line("audit findings", audit))
    return lines


def _counted(label: str, items: object, key: str) -> str:
    counts = Counter(
        str(item.get(key) or "-") for item in _list(items) if isinstance(item, Mapping)
    )
    return _counts_line(label, counts)


def _counts_line(label: str, counts: Counter[str]) -> str:
    total = sum(counts.values())
    if not total:
        return f"  {label}: 0"
    detail = ", ".join(f"{key} x{count}" for key, count in counts.most_common())
    return f"  {label}: {total} ({detail})"


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []
