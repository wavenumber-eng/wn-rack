"""Trace a self-declared test to the code each implementation runs.

A suite keeps one operation registry (``[operations] registry`` in rack.toml,
a TOML file relative to the project root). For every operation a test can send
through ``impl.batch`` it says, per implementation:

- ``dispatch`` (once per implementation): the file whose dispatch table maps
  each operation to its handler;
- ``handler``: the function that runs the operation;
- ``calls``: the library functions the handler exercises.

A test declares the operations it uses in ``RACK["operations"]``. The chain
test -> operation -> dispatch line -> handler -> library function is then
checked statically, and every hop is reported with its file and line:

- ``run`` and its helpers construct exactly the declared operations;
- every declared operation is in the registry;
- every implemented implementation has a handler for it, found on a dispatch
  line that names both the operation and the handler;
- the handler, or a same-file function it reaches, names every call;
- every handler and call exists where the registry says.

Suspended implementations have their ``calls`` verified too; planned and not
applicable ones are shown but not checked.
"""

from __future__ import annotations

import ast
import functools
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rack.code_refs import find_dispatch_line, locate_code_ref, project_root, unreferenced_calls
from rack.declarations import CodeRef, ImplementationStatus, TestDeclaration

VERIFIED_STATUSES = ("implemented", "suspended")


class RegistryError(ValueError):
    """The operation registry is missing or malformed."""


@dataclass(frozen=True)
class Binding:
    """What one implementation runs for one operation."""

    handler: CodeRef | None
    calls: tuple[CodeRef, ...]


@dataclass(frozen=True)
class Operation:
    name: str
    wire: str
    result: str
    bindings: Mapping[str, Binding]


@dataclass(frozen=True)
class Registry:
    path: str
    dispatch: Mapping[str, tuple[str, str]]  # implementation -> (file, after)
    operations: Mapping[str, Operation]


@dataclass(frozen=True)
class Hop:
    """One link in a trace: what it is, where it is, and what is wrong with it."""

    role: str  # "dispatch", "handler", or "calls"
    file: str
    line: int
    name: str
    problem: str | None = None
    ref: CodeRef | None = None


@dataclass(frozen=True)
class ImplementationTrace:
    implementation: str
    status: str
    note: str
    hops: tuple[Hop, ...]
    problems: tuple[str, ...]


@dataclass(frozen=True)
class OperationTrace:
    operation: str
    wire: str
    result: str
    implementations: tuple[ImplementationTrace, ...]


@dataclass(frozen=True)
class TestTrace:
    declaration: TestDeclaration
    root: Path
    operations: tuple[OperationTrace, ...]
    problems: tuple[str, ...] = field(default=())

    @property
    def all_problems(self) -> list[str]:
        problems = list(self.problems)
        for operation in self.operations:
            for implementation in operation.implementations:
                problems.extend(implementation.problems)
        return problems

    def calls(self, implementation: str) -> list[CodeRef]:
        """The library functions an implementation exercises across the test's operations."""
        refs: list[CodeRef] = []
        for operation in self.operations:
            for entry in operation.implementations:
                if entry.implementation != implementation:
                    continue
                refs.extend(hop.ref for hop in entry.hops if hop.role == "calls" and hop.ref)
        return refs


def suite_registry(suite_root: Path, config: Mapping[str, object]) -> tuple[Path, Registry]:
    """The project root and operation registry a suite's rack.toml names."""
    root = project_root(suite_root, config)
    table = config.get("operations")
    relative = table.get("registry") if isinstance(table, Mapping) else None
    if not isinstance(relative, str) or not relative.strip():
        raise RegistryError("rack.toml needs [operations] registry to trace self-declared tests")
    return root, load_registry(root, relative)


def registry_for(test_path: Path) -> tuple[Path, Registry]:
    """The project root and registry of the suite that owns ``test_path``."""
    manifest = next(
        (d / "rack.toml" for d in test_path.resolve().parents if (d / "rack.toml").is_file()),
        None,
    )
    if manifest is None:
        raise RegistryError(f"{test_path.name}: no rack.toml above this test")
    return suite_registry(manifest.parent, _load_toml(*_stamp(manifest)))


def load_registry(root: Path, relative: str) -> Registry:
    path = root / relative
    if not path.is_file():
        raise RegistryError(f"operation registry {relative} does not exist")
    data = _load_toml(*_stamp(path))
    dispatch = data.get("dispatch", {})
    operations = data.get("operations", {})
    if not isinstance(dispatch, dict) or not isinstance(operations, dict):
        raise RegistryError(f"{relative}: needs [dispatch] and [operations.<Name>] tables")
    return Registry(
        path=relative,
        dispatch={
            str(name): _parse_dispatch(relative, str(name), value)
            for name, value in dispatch.items()
        },
        operations={
            str(name): _parse_operation(relative, str(name), entry)
            for name, entry in operations.items()
        },
    )


def operations_constructed(path: Path, names: Sequence[str]) -> set[str]:
    """Registered operation names the test module constructs anywhere."""
    known = set(names)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
            if name in known:
                found.add(name)
    return found


def trace_test(declaration: TestDeclaration, root: Path, registry: Registry) -> TestTrace:
    declared = declaration.operations
    constructed = operations_constructed(declaration.path, list(registry.operations))
    problems = [
        f"run constructs {name} but RACK operations does not declare it"
        for name in sorted(constructed - set(declared))
    ]
    problems += [
        f"RACK operations declares {name} but run never constructs it"
        for name in declared
        if name not in constructed and name in registry.operations
    ]
    problems += [
        f"operation {name} is not in {registry.path}"
        for name in declared
        if name not in registry.operations
    ]
    traces = tuple(
        _trace_operation(registry.operations[name], declaration, root, registry)
        for name in declared
        if name in registry.operations
    )
    return TestTrace(declaration, root, traces, tuple(problems))


def trace_declaration(declaration: TestDeclaration) -> TestTrace | None:
    """Trace a test through its suite's registry; None for checks."""
    if declaration.kind == "check":
        return None
    root, registry = registry_for(declaration.path)
    return trace_test(declaration, root, registry)


def _trace_operation(
    operation: Operation, declaration: TestDeclaration, root: Path, registry: Registry
) -> OperationTrace:
    return OperationTrace(
        operation=operation.name,
        wire=operation.wire,
        result=operation.result,
        implementations=tuple(
            _trace_implementation(operation, status, root, registry)
            for status in declaration.implementations
        ),
    )


def _trace_implementation(
    operation: Operation, status: ImplementationStatus, root: Path, registry: Registry
) -> ImplementationTrace:
    binding = operation.bindings.get(status.name)
    note = status.reason + (f" ({status.issue})" if status.issue else "")
    if binding is None:
        problems = (
            (f"{status.name}: no handler for {operation.name} in {registry.path}",)
            if status.status == "implemented"
            else ()
        )
        return ImplementationTrace(status.name, status.status, note, (), problems)
    hops = _hops(operation, status, binding, root, registry)
    verified = status.status in VERIFIED_STATUSES
    problems = tuple(f"{status.name}: {hop.problem}" for hop in hops if hop.problem and verified)
    return ImplementationTrace(status.name, status.status, note, hops, problems)


def _hops(
    operation: Operation,
    status: ImplementationStatus,
    binding: Binding,
    root: Path,
    registry: Registry,
) -> tuple[Hop, ...]:
    hops: list[Hop] = []
    handler = binding.handler
    if handler is None and status.status == "implemented":
        hops.append(Hop("handler", "", 0, "", f"no handler for {operation.name}"))
    if handler is not None:
        hops.append(_dispatch_hop(operation, status.name, handler, root, registry))
        located = locate_code_ref(root, handler)
        hops.append(
            Hop("handler", handler.file, located.line, _label(handler), located.problem, handler)
        )
    missing = set(unreferenced_calls(root, handler, binding.calls)) if handler else set()
    for call in binding.calls:
        located = locate_code_ref(root, call)
        problem = located.problem
        if problem is None and call in missing:
            problem = f"handler {handler.function if handler else ''} never calls {call.function}"
        hops.append(Hop("calls", call.file, located.line, _label(call), problem, call))
    return tuple(hops)


def _dispatch_hop(
    operation: Operation, implementation: str, handler: CodeRef, root: Path, registry: Registry
) -> Hop:
    file, after = registry.dispatch.get(implementation, ("", ""))
    if not file:
        return Hop("dispatch", "", 0, "", f"no [dispatch] {implementation} in {registry.path}")
    handler_name = handler.function.rsplit(".", 1)[-1].rsplit("::", 1)[-1]
    line = find_dispatch_line(root, file, (operation.name, handler_name), after)
    name = f"{operation.name} -> {handler_name}"
    if line == 0:
        return Hop("dispatch", file, 0, name, f"{file} has no line mapping {name}")
    return Hop("dispatch", file, line, name)


def _label(ref: CodeRef) -> str:
    """The name in its own language's notation: ``a.b.f`` or ``a::b::f``."""
    separator = "." if ref.file.endswith(".py") else "::"
    return f"{ref.module}{separator}{ref.function}"


def _parse_dispatch(registry: str, name: str, value: object) -> tuple[str, str]:
    if isinstance(value, str) and value:
        return value, ""
    if (
        isinstance(value, dict)
        and set(value) <= {"file", "after"}
        and isinstance(value.get("file"), str)
        and isinstance(value.get("after", ""), str)
    ):
        return value["file"], value.get("after", "")
    raise RegistryError(f"{registry}: dispatch.{name} must be a file or {{file, after}}")


def _parse_operation(registry: str, name: str, entry: object) -> Operation:
    if not isinstance(entry, dict):
        raise RegistryError(f"{registry}: operations.{name} must be a table")
    bindings = {
        key: _parse_binding(registry, f"{name}.{key}", value)
        for key, value in entry.items()
        if isinstance(value, dict)
    }
    return Operation(
        name=name,
        wire=str(entry.get("wire", "")),
        result=str(entry.get("result", "")),
        bindings=bindings,
    )


def _parse_binding(registry: str, where: str, entry: dict[str, object]) -> Binding:
    handler = entry.get("handler")
    calls = entry.get("calls", [])
    if handler is not None and not isinstance(handler, dict):
        raise RegistryError(f"{registry}: {where}.handler must be a table")
    if not isinstance(calls, list):
        raise RegistryError(f"{registry}: {where}.calls must be a list")
    return Binding(
        handler=_code_ref(registry, where, handler) if handler is not None else None,
        calls=tuple(_code_ref(registry, where, call) for call in calls),
    )


def _code_ref(registry: str, where: str, value: object) -> CodeRef:
    if (
        not isinstance(value, dict)
        or set(value) != {"file", "module", "function"}
        or not all(isinstance(value[key], str) and value[key] for key in value)
    ):
        raise RegistryError(f"{registry}: {where} entries need exactly file, module, and function")
    return CodeRef(value["file"], value["module"], value["function"])


def _stamp(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size


@functools.lru_cache(maxsize=256)
def _load_toml(path: str, _mtime_ns: int, _size: int) -> dict[str, object]:
    try:
        with Path(path).open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise RegistryError(f"{Path(path).name} is invalid TOML: {error}") from error


def trace_problems(declaration: TestDeclaration) -> list[str]:
    """Every broken hop in the test's trace, or why it cannot be traced."""
    try:
        trace = trace_declaration(declaration)
    except (RegistryError, OSError, SyntaxError) as error:
        return [str(error)]
    return trace.all_problems if trace is not None else []


def declared_calls(declaration: TestDeclaration) -> dict[str, list[CodeRef]]:
    """The library functions each implementation exercises, from the registry."""
    try:
        trace = trace_declaration(declaration)
    except (RegistryError, OSError, SyntaxError):
        return {}
    if trace is None:
        return {}
    return {entry.name: trace.calls(entry.name) for entry in declaration.implementations}
