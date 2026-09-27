"""Static reading and validation of self-declared test files.

A self-declared test file carries a module docstring, one top-level
``RACK = {...}`` literal, and a single ``run(case, impl)`` entry point. Rack
reads all of it with ``ast`` so listing, auditing, and accounting never import
the test module.
"""

from __future__ import annotations

import ast
import base64
import functools
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ID_PATTERN = re.compile(r"^L\d+_\d{3}[a-z]?$")
STATUSES = ("implemented", "planned", "suspended", "not_applicable")
EXPECT_SOURCES = ("authority", "contract", "property", "budget")
DIFFERENCE_KINDS = (
    "missing",
    "extra",
    "type",
    "value",
    "length",
    "nonfinite",
    "unsupported_operation",
)
PROVENANCE_KINDS = ("authority", "specification", "generated")
VECTOR_SCHEMA = "rack.cases.a0"

_KNOWN_KEYS = frozenset(
    {
        "id",
        "title",
        "kind",
        "concerns",
        "code_under_test",
        "cases",
        "observation",
        "expect",
        "implementations",
        "deferred",
        "objectives",
        "approach",
        "test_case_type",
        "progress",
        "runtime_profile",
    }
)


class DeclarationError(ValueError):
    """A self-declared test file violates the declaration rules."""


@dataclass(frozen=True)
class ImplementationStatus:
    name: str
    status: str
    reason: str = ""
    issue: str = ""


@dataclass(frozen=True)
class Difference:
    path: tuple[object, ...]
    kind: str
    expected: object = None
    actual: object = None


@dataclass(frozen=True)
class TestDeclaration:
    path: Path
    id: str
    title: str
    kind: str
    docstring: str
    raw: Mapping[str, object]
    implementations: tuple[ImplementationStatus, ...] = ()
    deferred: Mapping[str, Mapping[str, tuple[Difference, ...]]] = field(default_factory=dict)
    deferred_issues: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    @property
    def concerns(self) -> tuple[str, ...]:
        return tuple(str(value) for value in _as_list(self.raw.get("concerns", [])))

    @property
    def cases(self) -> Mapping[str, object]:
        value = self.raw.get("cases", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def expect(self) -> Mapping[str, object]:
        value = self.raw.get("expect", {})
        return value if isinstance(value, Mapping) else {}

    def status_of(self, implementation: str) -> ImplementationStatus | None:
        for entry in self.implementations:
            if entry.name == implementation:
                return entry
        return None


@dataclass(frozen=True)
class Case:
    id: str
    inputs: Mapping[str, object]
    expect: object
    lane: str


@dataclass(frozen=True)
class VectorSet:
    path: Path
    observation: str
    provenance: Mapping[str, object]
    cases: tuple[Case, ...]


def is_self_declared(path: Path) -> bool:
    """Return True when the file contains a top-level ``RACK`` assignment."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return False
    # Cheap filter first; the AST check rejects RACK text inside string literals.
    if "RACK" not in source:
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    return _rack_assignment(tree) is not None


def declared_test_files(directory: Path) -> tuple[Path, ...]:
    """Self-declared ``test_*.py`` files directly inside ``directory``."""
    return tuple(
        path
        for path in sorted(directory.glob("test_*.py"))
        if _cached_is_self_declared(*_stamp(path))
    )


def load_declaration(path: Path) -> TestDeclaration:
    """``read_declaration`` cached by path, size, and modification time."""
    return _cached_declaration(*_stamp(path))


def manifest_entries(directory: Path) -> dict[str, dict[str, object]]:
    """Manifest subtest entries for the readable self-declared files in a stratum.

    Unreadable declarations are left out here; ``rack audit`` and collection
    report them.
    """
    entries: dict[str, dict[str, object]] = {}
    for path in declared_test_files(directory):
        try:
            entries[path.name] = manifest_entry(load_declaration(path))
        except DeclarationError:
            continue
    return entries


def manifest_entry(declaration: TestDeclaration) -> dict[str, object]:
    """The subtest entry Rack reports for a self-declared file."""
    raw = declaration.raw
    cases = declaration.cases
    return {
        "id": declaration.id,
        "name": declaration.title or declaration.path.name,
        "description": declaration.docstring.strip().splitlines()[0],
        "concerns": list(declaration.concerns),
        "code_under_test": raw.get("code_under_test", {}),
        "objectives": raw.get("objectives", {}),
        "approach": raw.get("approach", {}),
        "test_functions": {},
        "bug_reference": None,
        "progress": raw.get("progress"),
        "runtime_profile": raw.get("runtime_profile", ""),
        "test_cases": str(cases.get("file", "")),
        "test_case_type": raw.get("test_case_type", ""),
        "rack": {
            "kind": declaration.kind,
            "cases": dict(cases),
            "expect_source": str(declaration.expect.get("source", "")),
            "implementations": {
                entry.name: {"status": entry.status, "reason": entry.reason, "issue": entry.issue}
                for entry in declaration.implementations
            },
            "deferred": {
                name: dict(per_case) for name, per_case in declaration.deferred_issues.items()
            },
        },
    }


def read_declaration(path: Path) -> TestDeclaration:
    """Read and validate a self-declared test file without importing it."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    node = _rack_assignment(tree)
    if node is None:
        raise DeclarationError(f"{path.name}: no top-level RACK declaration")
    try:
        raw = ast.literal_eval(node.value)
    except ValueError as error:
        raise DeclarationError(f"{path.name}: RACK must be a pure literal ({error})") from error
    if not isinstance(raw, dict):
        raise DeclarationError(f"{path.name}: RACK must be a dict literal")
    _check_keys(path, raw)
    docstring = ast.get_docstring(tree) or ""
    if not docstring.strip():
        raise DeclarationError(
            f"{path.name}: a module docstring stating the test's purpose is required"
        )
    test_id = _check_id(path, raw)
    kind = str(raw.get("kind", "test"))
    if kind not in ("test", "check"):
        raise DeclarationError(f"{path.name}: kind must be 'test' or 'check'")
    _check_entry_point(path, tree)
    implementations = _parse_implementations(path, raw, kind)
    _check_expect(path, raw)
    _check_cases_ref(path, raw)
    deferred, issues = _parse_deferred(path, raw, implementations)
    return TestDeclaration(
        path=path,
        id=test_id,
        title=str(raw.get("title", "")),
        kind=kind,
        docstring=docstring,
        raw=raw,
        implementations=implementations,
        deferred=deferred,
        deferred_issues=issues,
    )


def load_vector_file(path: Path) -> VectorSet:
    """Load a JSON vector file, decoding ``{"base64": ...}`` values to bytes."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeclarationError(f"{path.name}: unreadable vector file ({error})") from error
    if not isinstance(payload, dict) or payload.get("schema") != VECTOR_SCHEMA:
        raise DeclarationError(f"{path.name}: vector file schema must be {VECTOR_SCHEMA!r}")
    provenance = payload.get("provenance")
    _check_provenance(path, provenance)
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DeclarationError(f"{path.name}: a vector file needs a non-empty cases list")
    cases = tuple(_parse_case(path, index, item) for index, item in enumerate(raw_cases))
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise DeclarationError(f"{path.name}: case ids must be unique")
    return VectorSet(
        path=path,
        observation=str(payload.get("observation", "")),
        provenance=provenance if isinstance(provenance, dict) else {},
        cases=cases,
    )


def validate_deferrals(declaration: TestDeclaration, case_ids: tuple[str, ...]) -> None:
    """Fail when a deferral names a case that is not in the complete case list."""
    known = set(case_ids)
    if not known:
        raise DeclarationError(f"{declaration.path.name}: the case list is empty")
    for implementation, per_case in declaration.deferred.items():
        for case_id in per_case:
            if case_id not in known:
                raise DeclarationError(
                    f"{declaration.path.name}: deferral for unknown case {case_id!r} ({implementation})"
                )


def decode_value(value: object) -> object:
    """Recursively decode ``{"base64": "..."}`` objects to bytes."""
    if isinstance(value, dict):
        if set(value) == {"base64"} and isinstance(value["base64"], str):
            return base64.b64decode(value["base64"], validate=True)
        return {key: decode_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_value(item) for item in value]
    return value


def _stamp(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size


@functools.lru_cache(maxsize=8192)
def _cached_is_self_declared(path: str, _mtime_ns: int, _size: int) -> bool:
    return is_self_declared(Path(path))


@functools.lru_cache(maxsize=8192)
def _cached_declaration(path: str, _mtime_ns: int, _size: int) -> TestDeclaration:
    return read_declaration(Path(path))


def _rack_assignment(tree: ast.Module) -> ast.Assign | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "RACK" for target in node.targets
        ):
            return node
    return None


def _check_keys(path: Path, raw: Mapping[str, object]) -> None:
    unknown = sorted(set(raw) - _KNOWN_KEYS)
    if unknown:
        raise DeclarationError(f"{path.name}: unknown RACK keys {unknown}")


def _check_id(path: Path, raw: Mapping[str, object]) -> str:
    test_id = raw.get("id")
    if not isinstance(test_id, str) or not ID_PATTERN.match(test_id):
        raise DeclarationError(f"{path.name}: id must match L<stratum>_<nnn>")
    if not path.name.startswith(f"test_{test_id}_"):
        raise DeclarationError(f"{path.name}: file name must start with test_{test_id}_")
    return test_id


def _check_entry_point(path: Path, tree: ast.Module) -> None:
    run = _single_run_function(path, tree)
    names = [argument.arg for argument in run.args.args]
    if names != ["case", "impl"]:
        raise DeclarationError(f"{path.name}: run must take exactly (case, impl)")
    if any(_is_impl_name(node) for node in ast.walk(run)):
        raise DeclarationError(f"{path.name}: run must not branch on the implementation")


def _single_run_function(path: Path, tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef:
    functions = [
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    tests = [node.name for node in functions if node.name.startswith("test_")]
    if tests:
        raise DeclarationError(f"{path.name}: test_* functions are not allowed ({tests})")
    public = [node for node in functions if not node.name.startswith("_")]
    if [node.name for node in public] != ["run"]:
        raise DeclarationError(f"{path.name}: the only public function must be run(case, impl)")
    return public[0]


def _is_impl_name(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "name"
        and isinstance(node.value, ast.Name)
        and node.value.id == "impl"
    )


def _parse_implementations(
    path: Path, raw: Mapping[str, object], kind: str
) -> tuple[ImplementationStatus, ...]:
    value = raw.get("implementations")
    if kind == "check":
        if value:
            raise DeclarationError(f"{path.name}: a check declares no implementations")
        return ()
    if not isinstance(value, dict) or not value:
        raise DeclarationError(
            f"{path.name}: implementations must name at least one implementation"
        )
    return tuple(_parse_status(path, str(name), entry) for name, entry in value.items())


def _parse_status(path: Path, name: str, entry: object) -> ImplementationStatus:
    if entry == "implemented":
        return ImplementationStatus(name, "implemented")
    status = _reasoned_status(entry)
    if status is None:
        raise DeclarationError(
            f"{path.name}: {name} status must be 'implemented' or one of "
            "{planned|suspended|not_applicable: reason}"
        )
    status_name, reason, issue = status
    if status_name == "planned" and not issue:
        raise DeclarationError(f"{path.name}: planned {name} needs an issue")
    return ImplementationStatus(name, status_name, reason, issue)


def _reasoned_status(entry: object) -> tuple[str, str, str] | None:
    """Return (status, reason, issue) for a ``{status: reason, "issue": ...}`` entry."""
    if not isinstance(entry, dict):
        return None
    statuses = [key for key in entry if key in STATUSES and key != "implemented"]
    if len(statuses) != 1:
        return None
    reason = entry[statuses[0]]
    issue = entry.get("issue", "")
    if not isinstance(reason, str) or not reason.strip() or not isinstance(issue, str):
        return None
    return statuses[0], reason, issue


def _check_expect(path: Path, raw: Mapping[str, object]) -> None:
    if raw.get("kind", "test") == "check":
        return
    expect = raw.get("expect")
    if not isinstance(expect, dict) or expect.get("source") not in EXPECT_SOURCES:
        raise DeclarationError(f"{path.name}: expect.source must be one of {EXPECT_SOURCES}")
    source = expect["source"]
    if source == "budget":
        _check_budget(path, expect)
        return
    if not isinstance(expect.get("comparator"), str):
        raise DeclarationError(f"{path.name}: expect.comparator is required")
    if source == "authority" and not isinstance(expect.get("loader"), str):
        raise DeclarationError(f"{path.name}: an authority expectation needs expect.loader")


def _check_budget(path: Path, expect: Mapping[str, object]) -> None:
    if not isinstance(expect.get("metric"), str) or not ("max" in expect or "max_ratio" in expect):
        raise DeclarationError(f"{path.name}: a budget needs a metric and max or max_ratio")
    if "max_ratio" in expect and not isinstance(expect.get("relative_to"), str):
        raise DeclarationError(f"{path.name}: max_ratio needs relative_to")


def _check_cases_ref(path: Path, raw: Mapping[str, object]) -> None:
    cases = raw.get("cases")
    if (
        not isinstance(cases, dict)
        or len(cases) != 1
        or not (isinstance(cases.get("file"), str) or isinstance(cases.get("catalog"), str))
    ):
        raise DeclarationError(f"{path.name}: cases must be {{'file': ...}} or {{'catalog': ...}}")


def _parse_deferred(
    path: Path,
    raw: Mapping[str, object],
    implementations: tuple[ImplementationStatus, ...],
) -> tuple[dict[str, dict[str, tuple[Difference, ...]]], dict[str, dict[str, str]]]:
    value = raw.get("deferred", {})
    if not isinstance(value, dict):
        raise DeclarationError(f"{path.name}: deferred must be a dict")
    implemented = {entry.name for entry in implementations if entry.status == "implemented"}
    deferred: dict[str, dict[str, tuple[Difference, ...]]] = {}
    issues: dict[str, dict[str, str]] = {}
    for name, per_case in value.items():
        if name not in implemented:
            raise DeclarationError(f"{path.name}: deferrals apply only to implemented {name!r}")
        if not isinstance(per_case, dict) or not per_case:
            raise DeclarationError(f"{path.name}: deferred[{name!r}] must map case ids")
        deferred[name] = {}
        issues[name] = {}
        for case_id, entry in per_case.items():
            differences, issue = _parse_deferral(path, name, str(case_id), entry)
            deferred[name][str(case_id)] = differences
            issues[name][str(case_id)] = issue
    return deferred, issues


def _parse_deferral(
    path: Path, name: str, case_id: str, entry: object
) -> tuple[tuple[Difference, ...], str]:
    if not isinstance(entry, dict):
        raise DeclarationError(f"{path.name}: deferral {name}/{case_id} must be a dict")
    issue = entry.get("issue")
    items = entry.get("differences")
    if not isinstance(issue, str) or not issue or not isinstance(items, list) or not items:
        raise DeclarationError(
            f"{path.name}: deferral {name}/{case_id} needs issue and differences"
        )
    return tuple(_parse_difference(path, item) for item in items), issue


def _parse_difference(path: Path, item: object) -> Difference:
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("path"), list)
        or item.get("kind") not in DIFFERENCE_KINDS
    ):
        raise DeclarationError(f"{path.name}: a difference needs a path list and a known kind")
    return Difference(
        tuple(item["path"]), str(item["kind"]), item.get("expected"), item.get("actual")
    )


def _check_provenance(path: Path, provenance: object) -> None:
    if (
        not isinstance(provenance, dict)
        or provenance.get("kind") not in PROVENANCE_KINDS
        or not isinstance(provenance.get("source"), str)
        or not provenance["source"].strip()
    ):
        raise DeclarationError(
            f"{path.name}: provenance needs a kind in {PROVENANCE_KINDS} and a source"
        )


def _parse_case(path: Path, index: int, item: object) -> Case:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
        raise DeclarationError(f"{path.name}: case {index} needs a string id")
    inputs = item.get("inputs", {})
    if not isinstance(inputs, dict):
        raise DeclarationError(f"{path.name}: case {item['id']} inputs must be an object")
    decoded = decode_value(inputs)
    assert isinstance(decoded, dict)
    return Case(
        id=item["id"],
        inputs=decoded,
        expect=decode_value(item.get("expect")),
        lane=str(item.get("lane", "fast")),
    )


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else []
