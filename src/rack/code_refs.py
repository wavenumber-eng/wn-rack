"""Static checks that named code exists, and where.

A code reference names a file relative to the suite's project root, the module
that file is, and a function the file defines. A check reads only that file
(and, for Rust, the package's Cargo.toml): nothing is imported, built, or run.
The file extension selects the checker, and an extension without one fails.
For C++ the module is the namespace the file opens.

Every check reports the line of the definition, so a trace can point at it.
"""

from __future__ import annotations

import ast
import functools
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from rack.declarations import CodeRef


@dataclass(frozen=True)
class Located:
    """Where a reference's definition is: a 1-based line, or why it was not found."""

    line: int = 0
    problem: str | None = None
    # "class" when a Python reference names a class; empty otherwise.
    kind: str = ""


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


def locate_code_ref(root: Path, ref: CodeRef) -> Located:
    """Find the definition ``ref`` names."""
    path = root / ref.file
    if not path.is_file():
        return Located(problem=f"{ref.file} does not exist")
    checker = _CHECKERS.get(path.suffix)
    if checker is None:
        return Located(problem=f"{ref.file}: no static check for '{path.suffix}' files")
    return checker(path, ref)


def check_code_ref(root: Path, ref: CodeRef) -> str | None:
    """Return why ``ref`` does not hold, or None when it does."""
    return locate_code_ref(root, ref).problem


def unreferenced_calls(root: Path, handler: CodeRef, calls: Sequence[CodeRef]) -> list[CodeRef]:
    """Calls the handler never names, directly or through helpers in its own file.

    A call counts as referenced when its last name segment (the function, the
    method, or the class) appears in the handler's body or in the body of a
    same-file function the handler reaches.
    """
    mentions = _MENTIONS.get((root / handler.file).suffix)
    if mentions is None:
        return list(calls)
    named = mentions(root / handler.file, _last_segment(handler.function))
    return [call for call in calls if _last_segment(call.function) not in named]


# A function definition that merely mentions a name is not a dispatch line.
_DEFINITION = re.compile(r"^\s*(?:async\s+def|def|(?:pub(?:\([^)]*\))?\s+)?fn)\s")


def find_dispatch_line(root: Path, file: str, words: Iterable[str], after: str = "") -> int:
    """The first non-definition line of ``file`` containing every word, or 0.

    With ``after``, the search starts below the first line containing that text,
    for files that hold several dispatch tables.
    """
    path = root / file
    if not path.is_file():
        return 0
    patterns = [re.compile(rf"(?<![\w]){re.escape(word)}(?![\w])") for word in words]
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = _start_after(lines, after)
    if start is None:
        return 0
    for number, line in enumerate(lines[start:], start=start + 1):
        if _maps(line, patterns):
            return number
    return 0


def _start_after(lines: Sequence[str], after: str) -> int | None:
    if not after:
        return 0
    return next((index + 1 for index, line in enumerate(lines) if after in line), None)


def _maps(line: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return not _DEFINITION.match(line) and all(pattern.search(line) for pattern in patterns)


def _last_segment(function: str) -> str:
    return re.split(r"::|\.", function)[-1]


def _check_python(path: Path, ref: CodeRef) -> Located:
    parts = ref.module.split(".")
    stem = path.parent if path.name == "__init__.py" else path.with_suffix("")
    if list(stem.parts[-len(parts) :]) != parts:
        return Located(problem=f"{ref.file} is not module {ref.module}")
    tree = _python_tree(*_stamp(path))
    if tree is None:
        return Located(problem=f"{ref.file} does not parse")
    return _python_symbol(tree.body, ref)


def _python_symbol(body: list[ast.stmt], ref: CodeRef) -> Located:
    *owners, name = ref.function.split(".")
    for owner in owners:
        classes = [node for node in body if isinstance(node, ast.ClassDef) and node.name == owner]
        if not classes:
            return Located(problem=f"{ref.file} defines no class {owner}")
        body = classes[0].body
    for node in body:
        if _defines(node, name):
            return Located(line=node.lineno, kind="class" if isinstance(node, ast.ClassDef) else "")
    return Located(problem=f"{ref.file} defines no {ref.function}")


def _defines(node: ast.stmt, name: str) -> bool:
    return (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == name
    )


def _python_mentions(path: Path, handler: str) -> set[str]:
    tree = _python_tree(*_stamp(path))
    if tree is None:
        return set()
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return _closure(handler, functions, _python_names)


def _python_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _check_rust(path: Path, ref: CodeRef) -> Located:
    crate, *modules = ref.module.split("::")
    problem = _rust_module_problem(path, ref, crate, modules)
    if problem is not None:
        return Located(problem=problem)
    source = _rust_source(*_stamp(path))
    owner, _, name = ref.function.rpartition("::")
    start = 0
    if owner:
        impl = re.search(rf"\bimpl\b[^{{;]*\b{re.escape(owner)}\b", source)
        if impl is None:
            return Located(problem=f"{ref.file} has no impl for {owner}")
        start = impl.end()
    found = re.compile(rf"\bfn\s+{re.escape(name)}\b").search(source, start)
    if found is None:
        return Located(problem=f"{ref.file} defines no fn {ref.function}")
    return Located(line=source.count("\n", 0, found.start()) + 1)


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


def _rust_mentions(path: Path, handler: str) -> set[str]:
    source = _rust_source(*_stamp(path))
    return _closure(handler, _rust_function_bodies(source), _rust_names)


def _rust_names(body: str) -> set[str]:
    return set(re.findall(r"\b\w+\b", body))


def _rust_function_bodies(source: str) -> dict[str, str]:
    bodies: dict[str, str] = {}
    for match in re.finditer(r"\bfn\s+(\w+)", source):
        opening = source.find("{", match.end())
        if opening != -1 and match.group(1) not in bodies:
            bodies[match.group(1)] = _braced(source, opening)
    return bodies


def _braced(source: str, opening: int) -> str:
    depth = 0
    for index in range(opening, len(source)):
        depth += {"{": 1, "}": -1}.get(source[index], 0)
        if depth == 0:
            return source[opening : index + 1]
    return source[opening:]


def _closure[T](
    handler: str, functions: Mapping[str, T], names_of: Callable[[T], set[str]]
) -> set[str]:
    """Every name the handler reaches, following calls to functions in the same file."""
    if handler not in functions:
        return set()
    seen = {handler}
    pending = [handler]
    named: set[str] = set()
    while pending:
        names = names_of(functions[pending.pop()])
        named |= names
        for name in names & functions.keys() - seen:
            seen.add(name)
            pending.append(name)
    return named


def _check_cpp(path: Path, ref: CodeRef) -> Located:
    source = _cpp_source(*_stamp(path))
    if not _opens_namespace(source, ref.module.split("::")):
        return Located(problem=f"{ref.file} does not open namespace {ref.module}")
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
    found = re.search(pattern, source, re.MULTILINE)
    if found is None:
        return Located(problem=f"{ref.file} defines no {ref.function}")
    return Located(line=source.count("\n", 0, found.start()) + 1)


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


def _keep_newlines(match: re.Match[str]) -> str:
    # Comments are blanked, not removed, so line numbers stay true.
    return "\n" * match.group().count("\n")


@functools.lru_cache(maxsize=4096)
def _rust_source(path: str, _mtime_ns: int, _size: int) -> str:
    return re.sub(r"//[^\n]*", "", Path(path).read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=4096)
def _cpp_source(path: str, _mtime_ns: int, _size: int) -> str:
    source = Path(path).read_text(encoding="utf-8", errors="replace")
    source = re.sub(r"/\*.*?\*/", _keep_newlines, source, flags=re.DOTALL)
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


_CHECKERS: dict[str, Callable[[Path, CodeRef], Located]] = {
    ".py": _check_python,
    ".rs": _check_rust,
    ".cpp": _check_cpp,
    ".cc": _check_cpp,
    ".h": _check_cpp,
    ".hpp": _check_cpp,
}

_MENTIONS: dict[str, Callable[[Path, str], set[str]]] = {
    ".py": _python_mentions,
    ".rs": _rust_mentions,
}
