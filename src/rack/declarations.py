"""Static reading and validation of self-declared test files.

A self-declared test file carries one top-level ``RACK = {...}`` literal,
including the test's required purpose. It is an ordinary pytest test with one
``test_*`` function, or, in the transitional adapter form, a single
``run(case, impl)`` entry point. Rack reads all of it with ``ast`` so listing,
auditing, and accounting never import the test module.
"""

from __future__ import annotations

import ast
import base64
import functools
import json
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

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
# Keys only a run(case, impl) file uses; a plain pytest test does its own work.
ADAPTER_ONLY_KEYS = ("cases", "observation", "expect", "operations", "deferred")
# Every rule a declaration must meet, in the order `rack audit` reports them.
REQUIREMENTS = (
    "rack_literal",
    "keys",
    "purpose",
    "id",
    "kind",
    "entry_point",
    "implementations",
    "operations",
    "expect",
    "cases_ref",
    "resources",
    "deferrals",
)

_KNOWN_KEYS = frozenset(
    {
        "id",
        "title",
        "kind",
        "concerns",
        "cases",
        "observation",
        "expect",
        "implementations",
        "operations",
        "purpose",
        "resources",
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
class CodeRef:
    """A function and the file that defines it, as the operation registry names it.

    ``file`` is relative to the suite's project root. ``function`` is a plain
    name or ``Type.method`` (Python) / ``Type::method`` (Rust, C++).
    """

    file: str
    module: str
    function: str


@dataclass(frozen=True)
class ImplementationStatus:
    name: str
    status: str
    reason: str = ""
    issue: str = ""
    # A native test in the implementation's own language, relative to the
    # project root; empty when this file's own test exercises it.
    test: str = ""
    # The functions this implementation's test exercises, verified statically.
    code: tuple[CodeRef, ...] = ()


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
    checks: str
    because: str
    raw: Mapping[str, object]
    implementations: tuple[ImplementationStatus, ...] = ()
    # "pytest": the file is an ordinary pytest test (one test_* function) and
    # Rack only reads its header. "adapter": run(case, impl) driven by Rack.
    form: str = "adapter"
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
    def resources(self) -> tuple[str, ...]:
        """Files the test reads (vectors, captures, corpus paths), as written in RACK."""
        return tuple(str(value) for value in _as_list(self.raw.get("resources", [])))

    @property
    def operations(self) -> tuple[str, ...]:
        """The registry operations ``run`` sends through ``impl.batch``."""
        return tuple(str(value) for value in _as_list(self.raw.get("operations", [])))

    @property
    def expect(self) -> Mapping[str, object]:
        value = self.raw.get("expect", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def in_file(self) -> tuple[ImplementationStatus, ...]:
        """Implementations without a native test (applicable ones), in header order."""
        return tuple(
            entry
            for entry in self.implementations
            if not entry.test and entry.status != "not_applicable"
        )

    @property
    def own(self) -> ImplementationStatus | None:
        """The implementation the file's test runs when it has no IMPLEMENTATIONS table.

        It is the first implementation listed without a native test; later ones
        without a test are counted only, since the header rules require them to
        be not implemented.
        """
        return self.in_file[0] if self.in_file else None

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
    # Imported here: tracing builds on declarations.
    from rack.tracing import declared_calls

    raw = declaration.raw
    cases = declaration.cases
    # Code listed in the header wins; otherwise the adapter form's registry trace.
    calls = listed_code(declaration) or declared_calls(declaration)
    return {
        "id": declaration.id,
        "name": declaration.title or declaration.path.name,
        "description": declaration.checks,
        "concerns": list(declaration.concerns),
        "code_under_test": _python_code_blocks(calls, _project_root(declaration.path)),
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
            "purpose": {"checks": declaration.checks, "because": declaration.because},
            "operations": list(declaration.operations),
            "cases": dict(cases),
            "expect_source": str(declaration.expect.get("source", "")),
            "implementations": {
                entry.name: {
                    "status": entry.status,
                    "reason": entry.reason,
                    "issue": entry.issue,
                    "code": [
                        {"file": ref.file, "module": ref.module, "function": ref.function}
                        for ref in calls.get(entry.name, [])
                    ],
                }
                for entry in declaration.implementations
            },
            "deferred": {
                name: dict(per_case) for name, per_case in declaration.deferred_issues.items()
            },
        },
    }


def listed_code(declaration: TestDeclaration) -> dict[str, list[CodeRef]]:
    """The code each implementation's header entry lists, by implementation."""
    return {entry.name: list(entry.code) for entry in declaration.implementations if entry.code}


def _project_root(test_file: Path) -> Path:
    """The project root code paths are relative to, for a test file in a stratum."""
    # Imported here: code_refs builds on declarations.
    from rack.code_refs import project_root

    suite_root = test_file.parent.parent
    config_path = suite_root / "rack.toml"
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        config = {}
    return project_root(suite_root, config)


def _python_code_blocks(calls: Mapping[str, list[CodeRef]], root: Path) -> list[dict[str, object]]:
    """Legacy ``code_under_test`` blocks for the Python code a test exercises.

    A plain name that the file defines as a class is reported as a class.
    """
    from rack.code_refs import locate_code_ref

    blocks: dict[str, dict[str, list[str]]] = {}
    for refs in calls.values():
        for ref in refs:
            if not ref.file.endswith(".py"):
                continue
            block = blocks.setdefault(ref.module, {"functions": [], "classes": [], "methods": []})
            owner, _, method = ref.function.rpartition(".")
            if owner:
                block["classes"].append(owner)
                block["methods"].append(method)
            elif locate_code_ref(root, ref).kind == "class":
                block["classes"].append(ref.function)
            else:
                block["functions"].append(ref.function)
    return [
        {"module": module, **{key: sorted(set(names)) for key, names in block.items() if names}}
        for module, block in blocks.items()
    ]


def read_declaration(path: Path) -> TestDeclaration:
    """Read and validate a self-declared test file without importing it."""
    results, problems = _evaluate(path)
    if problems:
        raise DeclarationError(next(iter(problems.values())))
    raw = cast(dict[str, object], results["raw"])
    checks, because = cast(tuple[str, str], results["purpose"])
    deferred, issues = cast(
        tuple[dict[str, dict[str, tuple[Difference, ...]]], dict[str, dict[str, str]]],
        results["deferrals"],
    )
    return TestDeclaration(
        path=path,
        id=cast(str, results["id"]),
        title=str(raw.get("title", "")),
        kind=cast(str, results["kind"]),
        form=cast(str, results["entry_point"]),
        checks=checks,
        because=because,
        raw=raw,
        implementations=cast(tuple[ImplementationStatus, ...], results["implementations"]),
        deferred=deferred,
        deferred_issues=issues,
    )


def declaration_problems(path: Path) -> dict[str, str]:
    """Every requirement in ``REQUIREMENTS`` the file fails, keyed by requirement.

    Each requirement is checked on its own, so one mistake does not hide the
    others. Deferrals are checked only when the implementations parse.
    """
    return _evaluate(path)[1]


def _evaluate(path: Path) -> tuple[dict[str, object], dict[str, str]]:
    try:
        tree, raw = _parse_rack(path)
    except DeclarationError as error:
        return {}, {"rack_literal": str(error)}
    kind = str(raw.get("kind", "test"))
    pytest_form = declaration_form(tree) == "pytest"
    steps: list[tuple[str, Callable[[], object]]] = [
        ("keys", lambda: _check_keys(path, raw, pytest_form)),
        ("purpose", lambda: _parse_purpose(path, raw)),
        ("id", lambda: _check_id(path, raw)),
        ("kind", lambda: _check_kind(path, kind)),
        ("entry_point", lambda: _check_entry_point(path, tree, raw)),
        ("implementations", lambda: _parse_implementations(path, raw, kind)),
        ("operations", lambda: _check_operations(path, raw, kind, pytest_form)),
        ("expect", lambda: _check_expect(path, raw, pytest_form)),
        ("cases_ref", lambda: _check_cases_ref(path, raw, pytest_form)),
        ("resources", lambda: _check_resources(path, raw)),
    ]
    results: dict[str, object] = {"raw": raw}
    problems: dict[str, str] = {}
    for name, step in steps:
        try:
            results[name] = step()
        except DeclarationError as error:
            problems[name] = str(error)
    if "implementations" in results:
        implementations = cast(tuple[ImplementationStatus, ...], results["implementations"])
        try:
            results["deferrals"] = _parse_deferred(path, raw, implementations)
        except DeclarationError as error:
            problems["deferrals"] = str(error)
    return results, problems


def _parse_rack(path: Path) -> tuple[ast.Module, dict[str, object]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as error:
        raise DeclarationError(f"{path.name}: unreadable test file ({error})") from error
    node = _rack_assignment(tree)
    if node is None:
        raise DeclarationError(f"{path.name}: no top-level RACK declaration")
    try:
        raw = ast.literal_eval(node.value)
    except ValueError as error:
        raise DeclarationError(f"{path.name}: RACK must be a pure literal ({error})") from error
    if not isinstance(raw, dict):
        raise DeclarationError(f"{path.name}: RACK must be a dict literal")
    return tree, raw


def _check_kind(path: Path, kind: str) -> str:
    if kind not in ("test", "check"):
        raise DeclarationError(f"{path.name}: kind must be 'test' or 'check'")
    return kind


def load_vector_file(path: Path) -> VectorSet:
    """Load a JSON vector file, decoding ``{"base64": ...}`` values to bytes."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeclarationError(f"{path.name}: unreadable vector file ({error})") from error
    if not isinstance(payload, dict) or payload.get("schema") != VECTOR_SCHEMA:
        raise DeclarationError(f"{path.name}: vector file schema must be {VECTOR_SCHEMA!r}")
    provenance = payload.get("provenance")
    check_provenance(path, provenance)
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


PURPOSE_MIN_WORDS = 3


def _parse_purpose(path: Path, raw: Mapping[str, object]) -> tuple[str, str]:
    """Return (checks, because): what the test verifies and why it matters.

    Both are required prose, so a reviewer can judge whether the test still
    earns its place; empty or one-word placeholders fail.
    """
    purpose = raw.get("purpose")
    if not isinstance(purpose, dict) or set(purpose) != {"checks", "because"}:
        raise DeclarationError(f"{path.name}: purpose must be {{'checks': ..., 'because': ...}}")
    for key in ("checks", "because"):
        value = purpose[key]
        if not isinstance(value, str) or len(value.split()) < PURPOSE_MIN_WORDS:
            raise DeclarationError(
                f"{path.name}: purpose.{key} must say it in at least {PURPOSE_MIN_WORDS} words"
            )
    return str(purpose["checks"]).strip(), str(purpose["because"]).strip()


def _check_keys(path: Path, raw: Mapping[str, object], pytest_form: bool) -> None:
    unknown = sorted(set(raw) - _KNOWN_KEYS)
    if unknown:
        raise DeclarationError(f"{path.name}: unknown RACK keys {unknown}")
    adapter_only = sorted(set(raw) & set(ADAPTER_ONLY_KEYS)) if pytest_form else []
    if adapter_only:
        raise DeclarationError(
            f"{path.name}: {adapter_only} belong only to run(case, impl) tests; "
            "a pytest test reads its own reference files"
        )


def _check_id(path: Path, raw: Mapping[str, object]) -> str:
    test_id = raw.get("id")
    if not isinstance(test_id, str) or not ID_PATTERN.match(test_id):
        raise DeclarationError(f"{path.name}: id must match L<stratum>_<nnn>")
    if not path.name.startswith(f"test_{test_id}_"):
        raise DeclarationError(f"{path.name}: file name must start with test_{test_id}_")
    return test_id


def declaration_form(tree: ast.Module) -> str:
    """``adapter`` for a file with a top-level ``run`` and no ``test_*``, else ``pytest``."""
    names = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    has_test = any(name.startswith("test_") for name in names)
    return "adapter" if "run" in names and not has_test else "pytest"


def source_form(path: Path) -> str:
    """The form of a test file from its functions, even when its header is invalid."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return "pytest"
    return declaration_form(tree)


def _check_entry_point(path: Path, tree: ast.Module, raw: Mapping[str, object]) -> str:
    if declaration_form(tree) == "pytest":
        _check_pytest_entry_point(path, tree)
        if raw.get("kind", "test") == "test":
            _check_implementations_table(path, tree, raw)
        return "pytest"
    _check_adapter_entry_point(path, tree)
    return "adapter"


def _check_pytest_entry_point(path: Path, tree: ast.Module) -> None:
    tests = [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    ]
    if len(tests) != 1:
        raise DeclarationError(
            f"{path.name}: a test file has exactly one test_* function (found {tests})"
        )


def _check_implementations_table(path: Path, tree: ast.Module, raw: Mapping[str, object]) -> None:
    """Every implemented implementation has a row, and each row runs one implementation.

    Without a table the file's test runs the first implementation listed
    without a native ``test``; any later implemented one needs a native test or
    a table. A top-level ``IMPLEMENTATIONS`` dict maps every implementation
    without a native test (not-applicable ones left out) to its function, in
    header order, and the test indexes it only with its ``implementation``
    parameter, so no row compares one implementation with another.
    """
    keys = _implementations_table_keys(tree)
    in_file = _in_file_entries(raw.get("implementations"))
    if keys is None:
        extra = [name for name, status in in_file[1:] if status == "implemented"]
        if extra or _takes_implementation(tree):
            raise DeclarationError(
                f"{path.name}: implemented {extra} need a native test, or a top-level "
                "IMPLEMENTATIONS = {...} dict mapping each implementation to its function"
            )
        return
    names = [name for name, _ in in_file]
    if keys != names:
        raise DeclarationError(
            f"{path.name}: IMPLEMENTATIONS {keys} must list the RACK implementations "
            f"{names} in the same order"
        )
    _check_table_use(path, tree)


def _takes_implementation(tree: ast.Module) -> bool:
    """Whether the file's test_* function takes an ``implementation`` parameter."""
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        and "implementation" in [argument.arg for argument in node.args.args]
        for node in tree.body
    )


def _check_table_use(path: Path, tree: ast.Module) -> None:
    if not _takes_implementation(tree):
        raise DeclarationError(f"{path.name}: a test with IMPLEMENTATIONS takes implementation")
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == "IMPLEMENTATIONS"
            and isinstance(node.ctx, ast.Load)
            and not _one_implementation_use(node, parents.get(node))
        ):
            raise DeclarationError(
                f"{path.name}: line {node.lineno} uses IMPLEMENTATIONS other than as "
                "IMPLEMENTATIONS[implementation] or in parametrize"
            )


def _one_implementation_use(node: ast.Name, parent: ast.AST | None) -> bool:
    if isinstance(parent, ast.Subscript):
        index = parent.slice
        return isinstance(index, ast.Name) and index.id == "implementation"
    return (
        isinstance(parent, ast.Call)
        and node in parent.args
        and isinstance(parent.func, ast.Attribute)
        and parent.func.attr == "parametrize"
    )


def _implementations_table_keys(tree: ast.Module) -> list[object] | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "IMPLEMENTATIONS"
            for target in node.targets
        ):
            if not isinstance(node.value, ast.Dict):
                return None
            return [key.value for key in node.value.keys if isinstance(key, ast.Constant)]
    return None


def _in_file_entries(implementations: object) -> list[tuple[str, str]]:
    """(name, status) of each applicable implementation without a native test."""
    if not isinstance(implementations, dict):
        return []
    return [
        (str(name), str(entry.get("status")))
        for name, entry in implementations.items()
        if isinstance(entry, dict)
        and entry.get("status") != "not_applicable"
        and "test" not in entry
    ]


def _check_adapter_entry_point(path: Path, tree: ast.Module) -> None:
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


_STATUS_KEYS = frozenset({"status", "reason", "issue", "test", "code"})
_CODE_KEYS = frozenset({"file", "module", "function"})


def _parse_status(path: Path, name: str, entry: object) -> ImplementationStatus:
    if not isinstance(entry, dict) or entry.get("status") not in STATUSES:
        raise DeclarationError(f"{path.name}: {name} needs a status in {STATUSES}")
    unknown = sorted(set(entry) - _STATUS_KEYS)
    if unknown:
        raise DeclarationError(f"{path.name}: {name} has unknown keys {unknown}")
    status = str(entry["status"])
    reason = _text(entry.get("reason", ""))
    issue = _text(entry.get("issue", ""))
    test = _test_path(path, name, entry)
    if status != "implemented" and not reason.strip():
        raise DeclarationError(f"{path.name}: {status} {name} needs a reason")
    if status == "planned" and not issue:
        raise DeclarationError(f"{path.name}: planned {name} needs an issue")
    code = _parse_code(path, name, entry.get("code", []))
    return ImplementationStatus(name, status, reason, issue, test, code)


def _parse_code(path: Path, name: str, value: object) -> tuple[CodeRef, ...]:
    if not isinstance(value, list) or not all(_is_code_entry(item) for item in value):
        raise DeclarationError(
            f"{path.name}: {name} code entries need exactly file, module, and function"
        )
    return tuple(CodeRef(item["file"], item["module"], item["function"]) for item in value)


def _is_code_entry(item: object) -> bool:
    return (
        isinstance(item, dict)
        and set(item) == _CODE_KEYS
        and all(isinstance(item[key], str) and item[key].strip() for key in _CODE_KEYS)
    )


def _test_path(path: Path, name: str, entry: Mapping[str, object]) -> str:
    test = entry.get("test", "")
    if not isinstance(test, str) or ("test" in entry and not test.strip()):
        raise DeclarationError(f"{path.name}: {name} test must be a path")
    return test


def _check_operations(path: Path, raw: Mapping[str, object], kind: str, pytest_form: bool) -> None:
    # A pytest test with operations fails the keys rule instead.
    if pytest_form:
        return
    value = raw.get("operations")
    if kind == "check":
        if value is not None:
            raise DeclarationError(f"{path.name}: operations belong only to run(case, impl) tests")
        return
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        raise DeclarationError(
            f"{path.name}: operations must list the registry operations run sends"
        )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _check_expect(path: Path, raw: Mapping[str, object], pytest_form: bool) -> None:
    # A pytest test does its own comparison; an expect key fails the keys rule.
    if raw.get("kind", "test") == "check" or pytest_form:
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


def _check_resources(path: Path, raw: Mapping[str, object]) -> None:
    value = raw.get("resources", [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise DeclarationError(f"{path.name}: resources must be a list of paths")


def _check_cases_ref(path: Path, raw: Mapping[str, object], pytest_form: bool) -> None:
    if pytest_form:
        return
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


def check_provenance(path: Path, provenance: object) -> None:
    """A reference file says where its values came from: a kind and a source."""
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
