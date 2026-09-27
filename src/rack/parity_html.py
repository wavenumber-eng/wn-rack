"""HTML sections of the Rack report built from a parity report."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape

_NOT_IMPLEMENTED = {"planned": "planned", "suspended": "suspended", "not_applicable": "n/a"}


def render_parity_html(report: Mapping[str, object]) -> str:
    """The PARITY section: implementation grid, tests by case, and debt."""
    totals = _mapping(report.get("totals"))
    if not totals.get("declared"):
        return ""
    implementations = [str(name) for name in _sequence(report.get("implementations"))]
    return f"""
    <div class="stratum-section collapsible">
        <div class="collapsible-header stratum-header">
            <h2>
                <span>PARITY</span>
                <span class="subtest-count">{totals.get("declared")} declared,
                {totals.get("legacy")} legacy, {totals.get("invalid")} invalid</span>
            </h2>
            <span class="toggle-icon">&#9654;</span>
        </div>
        <div class="collapsible-content">
            {_grid_card(report, implementations)}
            {_tests_card(report, implementations)}
            {_debt_card(_mapping(report.get("debt")))}
        </div>
    </div>
    """


def _grid_card(report: Mapping[str, object], implementations: Sequence[str]) -> str:
    header = "".join(f"<th>{escape(name)}</th>" for name in implementations)
    rows = "".join(
        _grid_row(_mapping(group), implementations)
        for group in [*_sequence(report.get("groups")), report.get("totals")]
    )
    return f"""
    <div class="info-card">
        <h4>Implementation grid (by {escape(str(report.get("group_by")))})</h4>
        <p class="file-path">Tests: implemented / planned / suspended / n/a.
        Cases: pass of total for implemented tests, then fail and deferred.</p>
        <table>
            <tr><th>Group</th><th>Declared</th><th>Legacy</th><th>Checks</th>{header}</tr>
            {rows}
        </table>
    </div>
    """


def _grid_row(group: Mapping[str, object], implementations: Sequence[str]) -> str:
    summaries = _mapping(group.get("implementations"))
    checks = _mapping(group.get("checks"))
    check_text = f"{checks.get('pass', 0)}/{checks.get('files', 0)}" if checks.get("files") else ""
    cells = "".join(
        f"<td>{_grid_cell(_mapping(summaries.get(name)))}</td>" for name in implementations
    )
    return (
        f"<tr><td>{escape(str(group.get('name')))}</td><td>{group.get('declared')}</td>"
        f"<td>{group.get('legacy')}</td><td>{check_text}</td>{cells}</tr>"
    )


def _grid_cell(summary: Mapping[str, object]) -> str:
    tests = _mapping(summary.get("tests"))
    cases = _mapping(summary.get("cases"))
    total = cases.get("total")
    counts = "/".join(
        str(tests.get(key, 0)) for key in ("implemented", "planned", "suspended", "not_applicable")
    )
    passed = f"{cases.get('pass', 0)}/{'?' if total is None else total}"
    problems = int(str(cases.get("fail", 0))) + int(str(cases.get("error", 0)))
    badge = "fail" if problems else ("skip" if cases.get("deferred") else "pass")
    return (
        f"{counts}<br><span class='badge badge-{badge}'>{passed}</span> "
        f"{problems} fail, {cases.get('deferred', 0)} deferred"
    )


def _tests_card(report: Mapping[str, object], implementations: Sequence[str]) -> str:
    tests = [
        _mapping(test)
        for test in _sequence(report.get("tests"))
        if _mapping(test).get("kind") in ("test", "check")
    ]
    blocks = "".join(_test_block(test, implementations) for test in tests)
    return f"""
    <div class="info-card">
        <h4>Declared tests</h4>
        <p class="file-path">Latest outcome per implementation; expand a test for its cases.</p>
        {blocks}
    </div>
    """


def _test_block(test: Mapping[str, object], implementations: Sequence[str]) -> str:
    cells = _mapping(test.get("cells"))
    columns = [name for name in [*implementations, "check"] if name in cells]
    badges = " ".join(
        f"{escape(name)}: {_cell_badge(_mapping(cells.get(name)), test.get('case_count'))}"
        for name in columns
    )
    title = escape(f"{test.get('id')} {test.get('title') or test.get('file')}")
    return f"""
    <div class="collapsible" style="margin-bottom: 6px;">
        <div class="collapsible-header" style="padding: 6px 10px;">
            <span>{title} <span class="file-path">({escape(str(test.get("stratum")))})</span>
            {badges}</span>
            <span class="toggle-icon">&#9654;</span>
        </div>
        <div class="collapsible-content" style="padding: 8px;">
            {_case_table(cells, columns)}
        </div>
    </div>
    """


def _cell_badge(cell: Mapping[str, object], case_count: object) -> str:
    status = str(cell.get("status", ""))
    if status in _NOT_IMPLEMENTED:
        note = escape(str(cell.get("note") or ""))
        return f"<span class='badge badge-skip'>{_NOT_IMPLEMENTED[status]}</span> {note}"
    outcomes = _mapping(cell.get("outcomes"))
    failed = int(str(outcomes.get("fail", 0))) + int(str(outcomes.get("error", 0)))
    if failed:
        return f"<span class='badge badge-fail'>{failed} fail</span>"
    total = "?" if case_count is None else str(case_count)
    text = f"{outcomes.get('pass', 0)}/{total}"
    if outcomes.get("deferred"):
        return f"<span class='badge badge-skip'>{text}, {outcomes.get('deferred')} deferred</span>"
    return f"<span class='badge badge-pass'>{text}</span>"


def _case_table(cells: Mapping[str, object], columns: Sequence[str]) -> str:
    case_ids: list[str] = []
    for name in columns:
        for case in _mapping(_mapping(cells.get(name)).get("cases")):
            if case not in case_ids:
                case_ids.append(case)
    if not case_ids:
        return "<p class='file-path'>No results yet.</p>"
    header = "".join(f"<th>{escape(name)}</th>" for name in columns)
    rows = "".join(
        "<tr><td>"
        + escape(case)
        + "</td>"
        + "".join(
            f"<td>{escape(str(_mapping(_mapping(cells.get(name)).get('cases')).get(case, '')))}</td>"
            for name in columns
        )
        + "</tr>"
        for case in case_ids
    )
    return f"<table><tr><th>Case</th>{header}</tr>{rows}</table>"


def _debt_card(debt: Mapping[str, object]) -> str:
    sections = [
        ("Deferred cases", debt.get("deferred"), ("test", "implementation", "case", "issue")),
        ("Planned", debt.get("planned"), ("test", "implementation", "note")),
        ("Suspended", debt.get("suspended"), ("test", "implementation", "note")),
        ("Failing rows", debt.get("failing"), ("test", "case", "implementation")),
        ("Audit findings", debt.get("audit"), ("code", "path", "message")),
    ]
    tables = "".join(_debt_table(title, items, keys) for title, items, keys in sections)
    legacy = len(_sequence(debt.get("legacy_files")))
    return f"""
    <div class="info-card">
        <h4>Debt</h4>
        <p>Legacy test files: {legacy}</p>
        {tables}
    </div>
    """


def _debt_table(title: str, items: object, keys: Sequence[str]) -> str:
    records = [_mapping(item) for item in _sequence(items)]
    if not records:
        return f"<p>{escape(title)}: 0</p>"
    header = "".join(f"<th>{escape(key)}</th>" for key in keys)
    rows = "".join(
        "<tr>" + "".join(f"<td>{escape(str(record.get(key, '')))}</td>" for key in keys) + "</tr>"
        for record in records
    )
    return f"<p>{escape(title)}: {len(records)}</p><table><tr>{header}</tr>{rows}</table>"


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, list) else []
