"""Static checks that declared code exists where a test says it does.

Each code entry names a file relative to the suite's project root, the module
that file is, and a function the file defines. A check reads only that file
(and, for Rust, the package's Cargo.toml): nothing is imported, built, or run.
The file extension selects the checker, and an extension without one fails.
For C++ the module is the namespace the file opens.
"""

from __future__ import annotations

import ast
import functools
import re
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path

from rack.declarations import CodeRef, TestDeclaration


def project_root(suite_root: Path, config: Mapping[str, object]) -> Path:
    """``[rack] project_root`` relative to rack.toml, else the nearest pyproject.toml."""
    rack = config.get("rack")
    value = rack.get("project_root") if isinstance(rack, Mapping) else None
    if isinstance(value, str) and value.strip():
        return (suite_root / value).resolve()
    for directory in (suite_root, *suite_root.parents):
        if (directory / "pyproject.toml").is_file():
            return directory
    return suite_root.parent


# Suspended code still exists and must stay where the declaration says; planned
# code may not exist yet, so it is not checked.
VERIFIED_STATUSES = ("implemented", "suspended")


def declaration_code_problems(root: Path, declaration: TestDeclaration) -> list[str]:
    """Problems with the code that implemented and suspended implementations declare."""
    problems: list[str] = []
    for entry in declaration.implementations:
        if entry.status not in VERIFIED_STATUSES:
            continue
        for ref in entry.code:
            problem = check_code_ref(root, ref)
            if problem is not None:
                problems.append(f"{entry.name}: {problem}")
    return problems


def check_code_ref(root: Path, ref: CodeRef) -> str | None:
    """Return why ``ref`` does not hold, or None when it does."""
    path = root / ref.file
    if not path.is_file():
        return f"{ref.file} does not exist"
    checker = _CHECKERS.get(path.suffix)
    if checker is None:
        return f"{ref.file}: no static check for '{path.suffix}' files"
    return checker(path, ref)


def _check_python(path: Path, ref: CodeRef) -> str | None:
    parts = ref.module.split(".")
    stem = path.parent if path.name == "__init__.py" else path.with_suffix("")
    if list(stem.parts[-len(parts) :]) != parts:
        return f"{ref.file} is not module {ref.module}"
    tree = _python_tree(*_stamp(path))
    if tree is None:
        return f"{ref.file} does not parse"
    return _python_symbol_problem(tree.body, ref)


def _python_symbol_problem(body: list[ast.stmt], ref: CodeRef) -> str | None:
    *owners, name = ref.function.split(".")
    for owner in owners:
        classes = [node for node in body if isinstance(node, ast.ClassDef) and node.name == owner]
        if not classes:
            return f"{ref.file} defines no class {owner}"
        body = classes[0].body
    if not any(_defines(node, name) for node in body):
        return f"{ref.file} defines no {ref.function}"
    return None


def _defines(node: ast.stmt, name: str) -> bool:
    return (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == name
    )


def _check_rust(path: Path, ref: CodeRef) -> str | None:
    crate, *modules = ref.module.split("::")
    problem = _rust_module_problem(path, ref, crate, modules)
    if problem is not None:
        return problem
    source = _rust_source(*_stamp(path))
    owner, _, name = ref.function.rpartition("::")
    if not re.search(rf"\bfn\s+{re.escape(name)}\b", source):
        return f"{ref.file} defines no fn {name}"
    if owner and not re.search(rf"\bimpl\b[^{{;]*\b{re.escape(owner)}\b", source):
        return f"{ref.file} has no impl for {owner}"
    return None


def _rust_module_problem(path: Path, ref: CodeRef, crate: str, modules: list[str]) -> str | None:
    manifest = next((d / "Cargo.toml" for d in path.parents if (d / "Cargo.toml").is_file()), None)
    if manifest is None:
        return f"{ref.file} is not inside a Cargo package"
    if crate not in _crate_names(*_stamp(manifest)):
        return f"{ref.file} is not in crate {crate}"
    try:
        relative = path.relative_to(manifest.parent / "src").with_suffix("")
    except ValueError:
        return f"{ref.file} is not under its package's src directory"
    segments = list(relative.parts)
    if segments[-1] == "mod" or segments in (["lib"], ["main"]):
        segments = segments[:-1]
    if segments != modules:
        return f"{ref.file} is not module {ref.module}"
    return None


def _check_cpp(path: Path, ref: CodeRef) -> str | None:
    source = _cpp_source(*_stamp(path))
    if not _opens_namespace(source, ref.module.split("::")):
        return f"{ref.file} does not open namespace {ref.module}"
    owner, _, name = ref.function.rpartition("::")
    if owner:
        # A qualified name outside a call site is a member definition.
        pattern = rf"\b{re.escape(owner)}::{re.escape(name)}\s*\("
    else:
        # A return type before the name, at the start of a line, is a
        # declaration or definition rather than a call.
        pattern = (
            rf"^(?![ \t]*(?:return|co_return|else|case|throw|delete|new)\b)"
            rf"[ \t]*[\w:<>,*& \t]+?[\s*&]{re.escape(name)}\s*\("
        )
    if not re.search(pattern, source, re.MULTILINE):
        return f"{ref.file} defines no {ref.function}"
    return None


def _opens_namespace(source: str, segments: list[str]) -> bool:
    if re.search(rf"\bnamespace\s+{re.escape('::'.join(segments))}\s*\{{", source):
        return True
    position = 0
    for segment in segments:
        found = re.compile(rf"\bnamespace\s+{re.escape(segment)}\s*\{{").search(source, position)
        if found is None:
            return False
        position = found.end()
    return True


def _stamp(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size


@functools.lru_cache(maxsize=4096)
def _python_tree(path: str, _mtime_ns: int, _size: int) -> ast.Module | None:
    try:
        return ast.parse(Path(path).read_text(encoding="utf-8"), filename=path)
    except (SyntaxError, UnicodeDecodeError):
        return None


@functools.lru_cache(maxsize=4096)
def _rust_source(path: str, _mtime_ns: int, _size: int) -> str:
    # Line comments are dropped so a commented-out fn does not count.
    return re.sub(r"//[^\n]*", "", Path(path).read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=4096)
def _cpp_source(path: str, _mtime_ns: int, _size: int) -> str:
    # Comments are dropped so commented-out code does not count.
    source = Path(path).read_text(encoding="utf-8", errors="replace")
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


@functools.lru_cache(maxsize=1024)
def _crate_names(path: str, _mtime_ns: int, _size: int) -> frozenset[str]:
    with Path(path).open("rb") as handle:
        manifest = tomllib.load(handle)
    names = set()
    for table in ("package", "lib"):
        entry = manifest.get(table)
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            names.add(entry["name"].replace("-", "_"))
    return frozenset(names)


_CHECKERS: dict[str, Callable[[Path, CodeRef], str | None]] = {
    ".py": _check_python,
    ".rs": _check_rust,
    ".cpp": _check_cpp,
    ".cc": _check_cpp,
    ".h": _check_cpp,
    ".hpp": _check_cpp,
}
