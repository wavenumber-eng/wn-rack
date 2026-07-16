"""Rack manifest audit API."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

AUDIT_REPORT_TYPE = "rack.audit_report"
AUDIT_REPORT_VERSION = "a0"
DEFAULT_SIGNOFF_STRATA = ("L99_signoff",)
IGNORED_TEST_DIR_NAMES = {"__pycache__", "rack_results"}


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
    if rack_config is not None:
        _validate_extra_strata(tests_root, strata, failures)
        for stratum in selected_strata:
            _validate_stratum(tests_root, stratum, strict, failures)
        if target_stratum is None:
            _validate_signoff_strata(tests_root, strata, configured_signoff, failures)

    return AuditReport(
        root=tests_root,
        strict=strict,
        signoff_strata=tuple(configured_signoff),
        target_stratum=target_stratum,
        failures=tuple(failures),
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
    return tuple(item.strip() for item in cast(list[object], order) if _non_empty_string(item))


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

    subtests = _manifest_subtests(root, stratum, manifest, strict, failures)
    _validate_unique_field(stratum, subtests, "id", "duplicate_subtest_id", failures)
    _validate_unique_field(stratum, subtests, "file", "duplicate_subtest_file", failures)
    _validate_subtest_files(root, stratum, subtests, failures)


def _manifest_subtests(
    root: Path,
    stratum: str,
    manifest: Mapping[str, object],
    strict: bool,
    failures: list[AuditFailure],
) -> tuple[Mapping[str, object], ...]:
    subtests_raw = manifest.get("subtests")
    manifest_path = f"{stratum}/STRATUM.toml"
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
    failures: list[AuditFailure],
) -> None:
    stratum_dir = root / stratum
    manifest_files = {
        file_value
        for subtest in subtests
        if (file_value := _string_value(subtest.get("file")))
    }
    discovered_files = {path.name for path in stratum_dir.glob("test_*.py")}
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
        if not _manifest_has_subtests(manifest):
            failures.append(
                _failure(
                    "empty_signoff_stratum",
                    f"{signoff_stratum} must declare signoff subtests",
                    f"{signoff_stratum}/STRATUM.toml",
                    stratum=signoff_stratum,
                )
            )


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
