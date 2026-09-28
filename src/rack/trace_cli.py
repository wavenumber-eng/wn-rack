"""CLI adapter for ``rack trace``: show what each implementation runs for a test."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from rack.declarations import DeclarationError, declared_test_files, load_declaration
from rack.tracing import Hop, OperationTrace, RegistryError, TestTrace, trace_declaration


def cmd_trace(args, tests_dir: Path, strata: Sequence[str]) -> int:
    """Print the trace of one test (by id) or of every self-declared test in a stratum."""
    paths = _select(tests_dir, strata, str(args.target))
    if not paths:
        print(f"No self-declared test or stratum matches {args.target}")
        return 1
    traces: list[TestTrace] = []
    for path in paths:
        try:
            trace = trace_declaration(load_declaration(path))
        except (DeclarationError, RegistryError) as error:
            print(f"{path.name}: {error}")
            return 1
        if trace is not None:
            traces.append(trace)
    if args.format == "json":
        print(json.dumps([trace_to_json(trace) for trace in traces], indent=2))
    else:
        print("\n\n".join(format_trace(trace) for trace in traces))
    return 0 if all(not trace.all_problems for trace in traces) else 1


def _select(tests_dir: Path, strata: Sequence[str], target: str) -> list[Path]:
    matching = [s for s in strata if s == target or s.startswith(f"{target}_")]
    if matching:
        return list(declared_test_files(tests_dir / matching[0]))
    for stratum in strata:
        for path in declared_test_files(tests_dir / stratum):
            if path.name.startswith(f"test_{target}_"):
                return [path]
    return []


def format_trace(trace: TestTrace) -> str:
    declaration = trace.declaration
    lines = [
        f"{declaration.id}  {declaration.title}",
        f"  file     {_relative(trace.root, declaration.path)}",
        f"  checks   {declaration.checks}",
        f"  because  {declaration.because}",
        f"  cases    {_cases(declaration.cases)}",
        f"  expect   {_expect(declaration.expect)}",
    ]
    lines.extend(f"  problem  {problem}" for problem in trace.problems)
    width = _where_width(trace)
    for operation in trace.operations:
        lines.extend(_operation_lines(operation, width))
    problems = trace.all_problems
    lines.append("")
    lines.append(f"  trace {'complete' if not problems else f'has {len(problems)} problem(s)'}")
    return "\n".join(lines)


def _operation_lines(operation: OperationTrace, width: int) -> list[str]:
    shape = " -> ".join(part for part in (operation.wire, operation.result) if part)
    lines = ["", f"  operation {operation.operation}" + (f"  ({shape})" if shape else "")]
    for implementation in operation.implementations:
        header = f"    {implementation.implementation:<8}{implementation.status}"
        if implementation.note:
            header += f": {implementation.note}"
        lines.append(header)
        lines.extend(_hop_line(hop, width) for hop in implementation.hops)
        lines.extend(f"      PROBLEM  {problem}" for problem in implementation.problems)
    return lines


def trace_to_json(trace: TestTrace) -> dict[str, object]:
    declaration = trace.declaration
    return {
        "id": declaration.id,
        "title": declaration.title,
        "file": _relative(trace.root, declaration.path),
        "purpose": {"checks": declaration.checks, "because": declaration.because},
        "operations": [
            {
                "operation": operation.operation,
                "wire": operation.wire,
                "result": operation.result,
                "implementations": [
                    {
                        "implementation": entry.implementation,
                        "status": entry.status,
                        "note": entry.note,
                        "hops": [
                            {
                                "role": hop.role,
                                "file": hop.file,
                                "line": hop.line,
                                "name": hop.name,
                                "problem": hop.problem,
                            }
                            for hop in entry.hops
                        ],
                    }
                    for entry in operation.implementations
                ],
            }
            for operation in trace.operations
        ],
        "problems": trace.all_problems,
    }


def _where(hop: Hop) -> str:
    return f"{hop.file}:{hop.line}" if hop.line else hop.file or "-"


def _where_width(trace: TestTrace) -> int:
    wheres = [
        _where(hop)
        for operation in trace.operations
        for implementation in operation.implementations
        for hop in implementation.hops
    ]
    return max([0, *(len(where) for where in wheres)])


def _hop_line(hop: Hop, width: int) -> str:
    return f"      {hop.role:<9}{_where(hop):<{width}}  {hop.name}"


def _cases(cases: object) -> str:
    if isinstance(cases, dict) and "file" in cases:
        return str(cases["file"])
    if isinstance(cases, dict) and "catalog" in cases:
        return f"catalog {cases['catalog']}"
    return "-"


def _expect(expect: object) -> str:
    if not isinstance(expect, dict):
        return "-"
    parts = [str(expect.get("source", ""))]
    for key in ("loader", "comparator"):
        if expect.get(key):
            parts.append(f"{key} {expect[key]}")
    return ", ".join(parts)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
