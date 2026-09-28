"""Rack manifest audit API."""

from __future__ import annotations

import ast
import json
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from rack.code_refs import check_code_ref, project_root
from rack.declaration_tally import DeclarationTally, require_declared, tally_declarations
from rack.declarations import (
    VECTOR_SCHEMA,
    DeclarationError,
    ImplementationStatus,
    TestDeclaration,
    check_provenance,
    declaration_problems,
    declared_test_files,
    load_declaration,
    load_vector_file,
    validate_deferrals,
)
from rack.tracing import trace_problems

AUDIT_REPORT_TYPE = "rack.audit_report"
AUDIT_REPORT_VERSION = "a1"
DEFAULT_SIGNOFF_STRATA = ("L99_signoff",)
IGNORED_TEST_DIR_NAMES = {"__pycache__", "rack_results"}
# Comments and string literals of Rust or C++ source, in source order.
_NATIVE_TOKENS = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:[^"\\\n]|\\.)*"', re.DOTALL)


@dataclass(frozen=True, slots=True)
class AuditFailure:
    """One Rack audit failure."""

    code: str
    message: str
    path: str
    stratum: str | None = None
    subtest: str | None = None

    def to_json_data(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""
        data: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "path": self.path,
        }
        if self.stratum is not None:
            data["stratum"] = self.stratum
        if self.subtest is not None:
            data["subtest"] = self.subtest
        return data


@dataclass(frozen=True, slots=True)
class AuditReport:
    """Rack audit report."""

    root: Path
    strict: bool
    signoff_strata: tuple[str, ...]
    target_stratum: str | None
    failures: tuple[AuditFailure, ...]
    declarations: DeclarationTally | None = None

    @property
    def passed(self) -> bool:
        """Return whether the audited suite passed."""
        return not self.failures

    def to_json_data(self) -> dict[str, object]:
        """Return the versioned JSON report contract."""
        data: dict[str, object] = {
            "type": AUDIT_REPORT_TYPE,
            "version": AUDIT_REPORT_VERSION,
            "root": self.root.as_posix(),
            "strict": self.strict,
            "signoff_strata": list(self.signoff_strata),
            "passed": self.passed,
            "failures": [failure.to_json_data() for failure in self.failures],
        }
        if self.target_stratum is not None:
            data["target_stratum"] = self.target_stratum
        if self.declarations is not None:
            data["declarations"] = self.declarations.to_json_data()
        return data


def audit_suite(
    root: Path,
    *,
    strict: bool = False,
    signoff_strata: Sequence[str] = DEFAULT_SIGNOFF_STRATA,
    target_stratum: str | None = None,
) -> AuditReport:
    """Audit a Rack suite for manifest-vs-filesystem drift."""
    tests_root = root.resolve()
    failures: list[AuditFailure] = []
    configured_signoff = _normalized_strings(signoff_strata) or DEFAULT_SIGNOFF_STRATA
    rack_config = _load_rack_config(tests_root, failures)
    strata = _rack_strata_order(rack_config) if rack_config is not None else ()
    if rack_config is not None and not strata:
        failures.append(
            _failure("missing_strata_order", "rack.toml must declare [strata].order", "rack.toml")
        )
    _validate_strata_order(strata, failures)

    selected_strata = _selected_strata(target_stratum, strata, failures)
    tally: DeclarationTally | None = None
    if rack_config is not None:
        _validate_extra_strata(tests_root, strata, failures)
        for stratum in selected_strata:
            _validate_stratum(tests_root, stratum, strict, failures)
        project = project_root(tests_root, rack_config)
        _validate_declared_suite(tests_root, project, selected_strata, strata, failures)
        if target_stratum is None:
            _validate_signoff_strata(tests_root, strata, configured_signoff, failures)
        tally = tally_declarations(tests_root, selected_strata, failures, registered=strata)

    return AuditReport(
        root=tests_root,
        strict=strict,
        signoff_strata=tuple(configured_signoff),
        target_stratum=target_stratum,
        failures=tuple(failures),
        declarations=tally,
    )


def _load_rack_config(root: Path, failures: list[AuditFailure]) -> Mapping[str, object] | None:
    if not root.exists():
        failures.append(_failure("missing_root", "Rack suite root does not exist", "."))
        return None
    if not root.is_dir():
        failures.append(_failure("root_not_directory", "Rack suite root is not a directory", "."))
        return None
    rack_path = root / "rack.toml"
    if not rack_path.exists():
        failures.append(_failure("missing_rack_toml", "rack.toml is required", "rack.toml"))
        return None
    return _load_toml(root, rack_path, failures)


def _load_toml(
    root: Path,
    path: Path,
    failures: list[AuditFailure],
    *,
    stratum: str | None = None,
) -> Mapping[str, object] | None:
    try:
        with path.open("rb") as handle:
            return cast(Mapping[str, object], tomllib.load(handle))
    except tomllib.TOMLDecodeError as exc:
        failures.append(
            _failure(
                "invalid_toml",
                f"{_relative(root, path)} is invalid TOML: {exc}",
                _relative(root, path),
                stratum=stratum,
            )
        )
    return None


def _rack_strata_order(config: Mapping[str, object]) -> tuple[str, ...]:
    strata_raw = config.get("strata")
    if not isinstance(strata_raw, dict):
        return ()
    order = cast(Mapping[str, object], strata_raw).get("order")
    if not isinstance(order, list):
        return ()
    values: list[str] = []
    for item in cast(list[object], order):
        if isinstance(item, str) and item.strip():
            values.append(item.strip())
    return tuple(values)


def _validate_strata_order(
    strata: tuple[str, ...],
    failures: list[AuditFailure],
) -> None:
    for duplicate in _duplicates(strata):
        failures.append(
            _failure(
                "duplicate_stratum",
                f"rack.toml has duplicate [strata].order entry: {duplicate}",
                "rack.toml",
                stratum=duplicate,
            )
        )


def _selected_strata(
    target_stratum: str | None,
    strata: tuple[str, ...],
    failures: list[AuditFailure],
) -> tuple[str, ...]:
    if target_stratum is None:
        return strata
    if target_stratum not in strata:
        failures.append(
            _failure(
                "unknown_stratum",
                f"stratum {target_stratum!r} is not declared in rack.toml",
                "rack.toml",
                stratum=target_stratum,
            )
        )
        return ()
    return (target_stratum,)


def _validate_extra_strata(
    root: Path,
    declared_strata: tuple[str, ...],
    failures: list[AuditFailure],
) -> None:
    declared = set(declared_strata)
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name in IGNORED_TEST_DIR_NAMES:
            continue
        if child.name in declared:
            continue
        if child.name.startswith("L") or (child / "STRATUM.toml").exists():
            failures.append(
                _failure(
                    "undeclared_stratum",
                    f"{child.name} looks like a stratum but is missing from rack.toml",
                    child.name,
                    stratum=child.name,
                )
            )


def _validate_stratum(
    root: Path,
    stratum: str,
    strict: bool,
    failures: list[AuditFailure],
) -> None:
    stratum_dir = root / stratum
    if not stratum_dir.exists():
        failures.append(
            _failure(
                "missing_stratum_directory",
                f"{stratum} directory is missing",
                stratum,
                stratum=stratum,
            )
        )
        return
    if not stratum_dir.is_dir():
        failures.append(
            _failure(
                "stratum_not_directory",
                f"{stratum} is not a directory",
                stratum,
                stratum=stratum,
            )
        )
        return

    manifest_path = stratum_dir / "STRATUM.toml"
    if not manifest_path.exists():
        failures.append(
            _failure(
                "missing_stratum_manifest",
                f"{stratum}/STRATUM.toml is required",
                f"{stratum}/STRATUM.toml",
                stratum=stratum,
            )
        )
        return
    manifest = _load_toml(root, manifest_path, failures, stratum=stratum)
    if manifest is None:
        return

    declared = {path.name for path in declared_test_files(stratum_dir)}
    subtests = _manifest_subtests(root, stratum, manifest, strict, bool(declared), failures)
    _validate_unique_field(stratum, subtests, "id", "duplicate_subtest_id", failures)
    _validate_unique_field(stratum, subtests, "file", "duplicate_subtest_file", failures)
    _validate_subtest_files(root, stratum, subtests, declared, failures)


def _manifest_subtests(
    root: Path,
    stratum: str,
    manifest: Mapping[str, object],
    strict: bool,
    has_declared: bool,
    failures: list[AuditFailure],
) -> tuple[Mapping[str, object], ...]:
    subtests_raw = manifest.get("subtests")
    manifest_path = f"{stratum}/STRATUM.toml"
    if subtests_raw is None and has_declared:
        return ()
    if not isinstance(subtests_raw, list):
        failures.append(
            _failure(
                "missing_subtests",
                f"{manifest_path} must declare [[subtests]]",
                manifest_path,
                stratum=stratum,
            )
        )
        return ()

    subtests: list[Mapping[str, object]] = []
    for index, raw_subtest in enumerate(cast(list[object], subtests_raw), start=1):
        if not isinstance(raw_subtest, dict):
            failures.append(
                _failure(
                    "invalid_subtest_entry",
                    f"{manifest_path} subtest {index} must be a table",
                    manifest_path,
                    stratum=stratum,
                )
            )
            continue
        subtest = cast(Mapping[str, object], raw_subtest)
        _validate_subtest_entry(root, stratum, index, subtest, strict, failures)
        subtests.append(subtest)
    return tuple(subtests)


def _validate_subtest_entry(
    root: Path,
    stratum: str,
    index: int,
    subtest: Mapping[str, object],
    strict: bool,
    failures: list[AuditFailure],
) -> None:
    manifest_path = f"{stratum}/STRATUM.toml"
    file_value = _string_value(subtest.get("file"))
    subtest_id = _string_value(subtest.get("id"))
    if not subtest_id:
        failures.append(
            _failure(
                "missing_subtest_id",
                f"{manifest_path} subtest {index} missing id",
                manifest_path,
                stratum=stratum,
                subtest=file_value,
            )
        )
    if not file_value:
        failures.append(
            _failure(
                "missing_subtest_file",
                f"{manifest_path} subtest {index} missing file",
                manifest_path,
                stratum=stratum,
                subtest=subtest_id,
            )
        )
    if strict:
        _validate_strict_metadata(root, stratum, subtest, file_value, subtest_id, failures)


def _validate_strict_metadata(
    root: Path,
    stratum: str,
    subtest: Mapping[str, object],
    file_value: str,
    subtest_id: str,
    failures: list[AuditFailure],
) -> None:
    label = file_value or subtest_id or "unknown subtest"
    if not _string_value(subtest.get("test_cases")):
        failures.append(
            _failure(
                "missing_test_cases",
                f"{stratum}/{label} missing test_cases declaration",
                f"{stratum}/STRATUM.toml",
                stratum=stratum,
                subtest=label,
            )
        )
    if not _string_value(subtest.get("test_case_type")):
        failures.append(
            _failure(
                "missing_test_case_type",
                f"{stratum}/{label} missing test_case_type declaration",
                f"{stratum}/STRATUM.toml",
                stratum=stratum,
                subtest=label,
            )
        )


def _validate_unique_field(
    stratum: str,
    subtests: Sequence[Mapping[str, object]],
    field: str,
    code: str,
    failures: list[AuditFailure],
) -> None:
    values: list[str] = []
    for subtest in subtests:
        value = _string_value(subtest.get(field))
        if value:
            values.append(value)
    duplicates = _duplicates(values)
    for value in duplicates:
        failures.append(
            _failure(
                code,
                f"{stratum}/STRATUM.toml has duplicate [[subtests]].{field}: {value}",
                f"{stratum}/STRATUM.toml",
                stratum=stratum,
                subtest=value,
            )
        )


def _validate_subtest_files(
    root: Path,
    stratum: str,
    subtests: Sequence[Mapping[str, object]],
    declared: set[str],
    failures: list[AuditFailure],
) -> None:
    stratum_dir = root / stratum
    manifest_files = {
        file_value for subtest in subtests if (file_value := _string_value(subtest.get("file")))
    }
    for file_name in sorted(manifest_files & declared):
        failures.append(
            _failure(
                "declared_file_in_manifest",
                f"{stratum}/STRATUM.toml has a subtest entry for self-declared {file_name}",
                f"{stratum}/STRATUM.toml",
                stratum=stratum,
                subtest=file_name,
            )
        )
    manifest_files -= declared
    discovered_files = {path.name for path in stratum_dir.glob("test_*.py")} - declared
    if require_declared(stratum_dir):
        for file_name in sorted(discovered_files):
            failures.append(
                _failure(
                    "undeclared_test_file",
                    f"{stratum} requires self-declared tests; {file_name} has no RACK declaration",
                    f"{stratum}/{file_name}",
                    stratum=stratum,
                    subtest=file_name,
                )
            )
    for file_name in sorted(discovered_files - manifest_files):
        failures.append(
            _failure(
                "missing_discovered_subtest",
                f"{stratum}/STRATUM.toml missing discovered test file: {file_name}",
                f"{stratum}/STRATUM.toml",
                stratum=stratum,
                subtest=file_name,
            )
        )
    for file_name in sorted(manifest_files - discovered_files):
        failures.append(
            _failure(
                "missing_declared_subtest_file",
                f"{stratum}/STRATUM.toml declares missing test file: {file_name}",
                f"{stratum}/{file_name}",
                stratum=stratum,
                subtest=file_name,
            )
        )


def _validate_signoff_strata(
    root: Path,
    strata: tuple[str, ...],
    signoff_strata: tuple[str, ...],
    failures: list[AuditFailure],
) -> None:
    declared = set(strata)
    for signoff_stratum in signoff_strata:
        if signoff_stratum not in declared:
            failures.append(
                _failure(
                    "missing_signoff_stratum",
                    f"rack.toml missing signoff stratum {signoff_stratum}",
                    "rack.toml",
                    stratum=signoff_stratum,
                )
            )
            continue
        manifest_path = root / signoff_stratum / "STRATUM.toml"
        manifest = _load_toml(root, manifest_path, failures, stratum=signoff_stratum)
        if manifest is None:
            continue
        if not _manifest_has_subtests(manifest) and not declared_test_files(root / signoff_stratum):
            failures.append(
                _failure(
                    "empty_signoff_stratum",
                    f"{signoff_stratum} must declare signoff subtests",
                    f"{signoff_stratum}/STRATUM.toml",
                    stratum=signoff_stratum,
                )
            )


def _validate_declared_suite(
    root: Path,
    project: Path,
    selected: tuple[str, ...],
    strata: tuple[str, ...],
    failures: list[AuditFailure],
) -> None:
    """Check self-declared files: declarations, cases, code, id collisions, test imports."""
    declarations = _suite_declarations(root, strata)
    for stratum in selected:
        for path in declared_test_files(root / stratum):
            declaration = _read_declared(root, stratum, path, failures)
            if declaration is not None:
                _validate_declared_cases(root, stratum, declaration, failures)
                _validate_declared_resources(root, stratum, declaration, failures)
                _validate_declared_code(root, project, stratum, declaration, failures)
                _validate_native_tests(root, project, stratum, declaration, failures)
    selected_paths = {path.resolve() for s in selected for path in declared_test_files(root / s)}
    local = [d for d in declarations if d.path.resolve() in selected_paths]
    _validate_declared_ids(root, strata, declarations, local, failures)
    _validate_test_imports(root, selected, declarations, failures)


def _validate_declared_resources(
    root: Path, stratum: str, declaration: TestDeclaration, failures: list[AuditFailure]
) -> None:
    for code, problem in resource_problems(declaration):
        failures.append(
            _failure(
                code,
                f"{declaration.path.name}: {problem}",
                _relative(root, declaration.path),
                stratum=stratum,
                subtest=declaration.path.name,
            )
        )


def resource_problems(declaration: TestDeclaration) -> list[tuple[str, str]]:
    """Each listed resource exists, a pytest test names it, and JSON states its provenance.

    A JSON resource with the vector schema is checked as a vector file; any
    other JSON resource, such as a captured authority file, needs a top-level
    ``provenance``. Other files are checked for existence only.
    """
    source = _source_outside_header(declaration.path) if declaration.form == "pytest" else None
    problems: list[tuple[str, str]] = []
    for item in declaration.resources:
        path = declaration.path.parent / item
        if not path.is_file():
            problems.append(("invalid_resource", f"resource {item} does not exist"))
            continue
        if source is not None and path.name not in source:
            problems.append(("invalid_resource", f"the test does not name resource {item}"))
        if path.suffix == ".json":
            problems += [("invalid_cases", problem) for problem in _json_resource_problems(path)]
    return problems


def _json_resource_problems(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return [f"{path.name}: unreadable JSON ({error})"]
    try:
        if isinstance(payload, dict) and payload.get("schema") == VECTOR_SCHEMA:
            load_vector_file(path)
        else:
            check_provenance(path, payload.get("provenance") if isinstance(payload, dict) else None)
    except DeclarationError as error:
        return [str(error)]
    return []


def _source_outside_header(path: Path) -> str:
    """The test file's source without its RACK assignment."""
    source = path.read_text(encoding="utf-8")
    header = next(
        (
            node
            for node in ast.parse(source).body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "RACK" for target in node.targets)
        ),
        None,
    )
    if header is None or header.end_lineno is None:
        return source
    lines = source.splitlines()
    return "\n".join(lines[: header.lineno - 1] + lines[header.end_lineno :])


def _validate_declared_code(
    root: Path,
    project: Path,
    stratum: str,
    declaration: TestDeclaration,
    failures: list[AuditFailure],
) -> None:
    for problem in [*trace_problems(declaration), *listed_code_problems(project, declaration)]:
        failures.append(
            _failure(
                "untraced",
                f"{declaration.path.name}: {problem}",
                _relative(root, declaration.path),
                stratum=stratum,
                subtest=declaration.path.name,
            )
        )


def _validate_native_tests(
    root: Path,
    project: Path,
    stratum: str,
    declaration: TestDeclaration,
    failures: list[AuditFailure],
) -> None:
    """A native test a header lists exists and reads the same vector files."""
    for problem in _native_test_problems(project, declaration):
        failures.append(
            _failure(
                "native_test",
                f"{declaration.path.name}: {problem}",
                _relative(root, declaration.path),
                stratum=stratum,
                subtest=declaration.path.name,
            )
        )


def listed_code_problems(project: Path, declaration: TestDeclaration) -> list[str]:
    """Header code entries of implemented and suspended implementations must resolve."""
    return [
        f"{entry.name}: {problem}"
        for entry in declaration.implementations
        if entry.status in ("implemented", "suspended")
        for ref in entry.code
        if (problem := check_code_ref(project, ref)) is not None
    ]


def _native_test_problems(project: Path, declaration: TestDeclaration) -> list[str]:
    problems: list[str] = []
    for entry in declaration.implementations:
        if entry.test and entry.status != "not_applicable":
            problems += _native_test_file_problems(project, declaration, entry)
    return problems


def _native_test_file_problems(
    project: Path, declaration: TestDeclaration, entry: ImplementationStatus
) -> list[str]:
    path = project / entry.test
    expected_file, expected_symbol = native_test_names(declaration, path.suffix)
    problems: list[str] = []
    if expected_file and path.name != expected_file:
        problems.append(f"{entry.name} test {entry.test} must be named {expected_file}")
    if path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
        problems += _native_content_problems(declaration, entry, text, expected_symbol)
    elif entry.status in ("implemented", "suspended"):
        # A planned native test may not exist yet; a suspended one keeps its file.
        problems.append(f"{entry.name} test {entry.test} does not exist")
    return problems


def _native_content_problems(
    declaration: TestDeclaration, entry: ImplementationStatus, text: str, symbol: str
) -> list[str]:
    problems: list[str] = []
    test_function = rf"#\[test\]\s*(?:#\[[^\]]*\]\s*)*fn {re.escape(symbol)}\s*\("
    if symbol and re.search(test_function, text) is None:
        problems.append(f"{entry.name} test {entry.test} must define #[test] fn {symbol}")
    # A suspended or planned test may predate the vector file; it must read it
    # once the implementation is implemented again.
    if entry.status != "implemented":
        return problems
    literals = " ".join(native_string_literals(text))
    vectors = [Path(item).name for item in declaration.resources if item.endswith(".json")]
    problems += [
        f"{entry.name} test {entry.test} does not read {name} (named in a string literal)"
        for name in vectors
        if name not in literals
    ]
    return problems


def native_string_literals(text: str) -> list[str]:
    """The string literals of Rust or C++ source, skipping comments."""
    return [token[1:-1] for token in _NATIVE_TOKENS.findall(text) if token.startswith('"')]


def native_test_names(declaration: TestDeclaration, suffix: str) -> tuple[str, str]:
    """The file name and test function a native test must use, by language.

    Both come from the test's id and the slug of its Python file name, so one
    test reads the same in every language: test_L0_004_parse_byte_record.py,
    test_l0_004_parse_byte_record.rs with fn l0_004_parse_byte_record, and
    test_L0_004_parse_byte_record.cpp.
    """
    slug = declaration.path.stem.removeprefix(f"test_{declaration.id}_")
    if suffix == ".rs":
        name = f"{declaration.id.lower()}_{slug}"
        return f"test_{name}.rs", name
    if suffix == ".cpp":
        return f"test_{declaration.id}_{slug}.cpp", ""
    return "", ""


def _suite_declarations(root: Path, strata: tuple[str, ...]) -> list[TestDeclaration]:
    declarations: list[TestDeclaration] = []
    for stratum in strata:
        for path in declared_test_files(root / stratum):
            try:
                declarations.append(load_declaration(path))
            except DeclarationError:
                continue
    return declarations


def _read_declared(
    root: Path, stratum: str, path: Path, failures: list[AuditFailure]
) -> TestDeclaration | None:
    try:
        return load_declaration(path)
    except DeclarationError:
        pass
    # Report every failing requirement, not only the first one.
    for requirement, message in declaration_problems(path).items():
        failures.append(
            _failure(
                "invalid_declaration",
                f"[{requirement}] {message}",
                _relative(root, path),
                stratum=stratum,
                subtest=path.name,
            )
        )
    return None


def _validate_declared_cases(
    root: Path, stratum: str, declaration: TestDeclaration, failures: list[AuditFailure]
) -> None:
    # Catalog cases need suite code to enumerate; collection validates them.
    file_ref = declaration.cases.get("file")
    if not isinstance(file_ref, str):
        return
    try:
        vectors = load_vector_file(declaration.path.parent / file_ref)
        validate_deferrals(declaration, tuple(case.id for case in vectors.cases))
    except DeclarationError as exc:
        failures.append(
            _failure(
                "invalid_cases",
                str(exc),
                _relative(root, declaration.path),
                stratum=stratum,
                subtest=declaration.path.name,
            )
        )


def _validate_declared_ids(
    root: Path,
    strata: tuple[str, ...],
    declarations: Sequence[TestDeclaration],
    local: Sequence[TestDeclaration],
    failures: list[AuditFailure],
) -> None:
    """A self-declared id is unique across the suite, legacy ids included.

    Duplicate ids between legacy entries in different strata stay out of scope.
    """
    owners: dict[str, list[str]] = {}
    for declaration in declarations:
        owners.setdefault(declaration.id, []).append(_relative(root, declaration.path))
    for stratum in strata:
        for test_id, file_name in _legacy_ids(root, stratum):
            if test_id in owners:
                owners[test_id].append(f"{stratum}/{file_name}")
    for test_id in sorted({declaration.id for declaration in local}):
        paths = sorted(owners[test_id])
        if len(paths) > 1:
            failures.append(
                _failure(
                    "duplicate_test_id",
                    f"test id {test_id} is used by {', '.join(paths)}",
                    paths[0],
                    subtest=test_id,
                )
            )


def _legacy_ids(root: Path, stratum: str) -> list[tuple[str, str]]:
    manifest_path = root / stratum / "STRATUM.toml"
    if not manifest_path.is_file():
        return []
    try:
        with manifest_path.open("rb") as handle:
            manifest = tomllib.load(handle)
    except tomllib.TOMLDecodeError:
        return []
    declared = {path.name for path in declared_test_files(root / stratum)}
    ids: list[tuple[str, str]] = []
    for subtest in manifest.get("subtests", []):
        if isinstance(subtest, dict):
            test_id = _string_value(subtest.get("id"))
            file_name = _string_value(subtest.get("file"))
            if test_id and file_name not in declared:
                ids.append((test_id, file_name))
    return ids


def _validate_test_imports(
    root: Path,
    selected: tuple[str, ...],
    declarations: Sequence[TestDeclaration],
    failures: list[AuditFailure],
) -> None:
    """No self-declared file imports a test module, and nothing imports one.

    Imports between legacy test files are left alone until they are converted.
    """
    declared_paths = {declaration.path.resolve() for declaration in declarations}
    if not declared_paths:
        return
    declared_stems = {path.stem for path in declared_paths}
    mention = re.compile("|".join(re.escape(stem) for stem in sorted(declared_stems)))
    for stratum in selected:
        for path in _python_files(root / stratum):
            is_declared = path.resolve() in declared_paths
            for module in _test_module_imports(path, is_declared, mention):
                if is_declared or module in declared_stems:
                    failures.append(
                        _failure(
                            "test_module_import",
                            f"{_relative(root, path)} imports test module {module}",
                            _relative(root, path),
                            stratum=stratum,
                        )
                    )


def _python_files(directory: Path) -> list[Path]:
    return [
        path
        for path in sorted(directory.rglob("*.py"))
        if not IGNORED_TEST_DIR_NAMES.intersection(path.parts)
    ]


def _test_module_imports(path: Path, is_declared: bool, mention: re.Pattern[str]) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if not is_declared and not mention.search(source):
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    return [name for name in _imported_leaf_names(tree) if name.startswith("test_")]


def _imported_leaf_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module.rsplit(".", 1)[-1])
            # "from . import test_x" names the module in the alias.
            names.extend(alias.name for alias in node.names)
    return names


def _manifest_has_subtests(manifest: Mapping[str, object]) -> bool:
    subtests = manifest.get("subtests")
    return isinstance(subtests, list) and bool(subtests)


def _failure(
    code: str,
    message: str,
    path: str,
    *,
    stratum: str | None = None,
    subtest: str | None = None,
) -> AuditFailure:
    return AuditFailure(code=code, message=message, path=path, stratum=stratum, subtest=subtest)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _normalized_strings(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(value.strip() for value in values if value.strip())


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _duplicates(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))
