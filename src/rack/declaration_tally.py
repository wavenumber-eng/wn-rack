"""Tally of a suite's test files against the self-declared test requirements.

Every ``test_*.py`` file in an audited stratum is counted as self-declared or
without ``RACK``. Test files anywhere else under the suite root (the root
itself, a stratum subdirectory, an unregistered directory) are listed as
outside the strata: Rack neither lists nor audits them, and ``rack run``
either skips them or runs them unaccounted. Each self-declared file is scored against every requirement:
the declaration rules in ``rack.declarations.REQUIREMENTS`` plus the suite
rules the audit checks (vector files, declared code, unique ids, imports, and
no STRATUM entry). Failing files are listed under each requirement so outliers
are visible at a glance.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from rack.declarations import REQUIREMENTS, declaration_problems, declared_test_files

IGNORED_DIR_NAMES = frozenset({"__pycache__", "rack_results", ".pytest_cache"})

# Audit failure codes that correspond to suite-level requirements.
SUITE_REQUIREMENTS = {
    "invalid_cases": "vectors",
    "invalid_resource": "resource_files",
    "untraced": "trace",
    "native_test": "native_tests",
    "duplicate_test_id": "unique_id",
    "test_module_import": "no_test_imports",
    "declared_file_in_manifest": "no_stratum_entry",
}
ALL_REQUIREMENTS = (*REQUIREMENTS, *SUITE_REQUIREMENTS.values())


class CodedFailure(Protocol):
    """The part of an audit failure the tally reads."""

    @property
    def code(self) -> str: ...

    @property
    def path(self) -> str: ...


@dataclass(frozen=True, slots=True)
class StratumTally:
    name: str
    test_files: int
    declared: tuple[str, ...]
    without_rack: tuple[str, ...]
    require_declared: bool

    def to_json_data(self) -> dict[str, object]:
        return {
            "name": self.name,
            "test_files": self.test_files,
            "self_declared": len(self.declared),
            "require_declared": self.require_declared,
            "without_rack": list(self.without_rack),
        }


@dataclass(frozen=True, slots=True)
class DeclarationTally:
    strata: tuple[StratumTally, ...]
    requirements: tuple[tuple[str, tuple[str, ...]], ...]
    outside_strata: tuple[str, ...] = ()

    @property
    def test_files(self) -> int:
        return sum(stratum.test_files for stratum in self.strata)

    @property
    def self_declared(self) -> int:
        return sum(len(stratum.declared) for stratum in self.strata)

    @property
    def without_rack(self) -> int:
        return sum(len(stratum.without_rack) for stratum in self.strata)

    def to_json_data(self) -> dict[str, object]:
        return {
            "test_files": self.test_files,
            "self_declared": self.self_declared,
            "without_rack": self.without_rack,
            "outside_strata": list(self.outside_strata),
            "strata": [stratum.to_json_data() for stratum in self.strata],
            "requirements": [
                {"name": name, "failing": list(files)} for name, files in self.requirements
            ],
        }


def tally_declarations(
    root: Path,
    strata: Sequence[str],
    failures: Sequence[CodedFailure],
    registered: Sequence[str] | None = None,
) -> DeclarationTally:
    """Count test files per audited stratum and the files failing each requirement.

    ``registered`` is every stratum in rack.toml (default: ``strata``); only files
    outside all of them count as outside the strata.
    """
    failing: dict[str, set[str]] = {name: set() for name in ALL_REQUIREMENTS}
    tallies: list[StratumTally] = []
    for stratum in strata:
        tally = _stratum_tally(root, stratum)
        tallies.append(tally)
        for name in tally.declared:
            for requirement in declaration_problems(root / stratum / name):
                failing[requirement].add(f"{stratum}/{name}")
    for failure in failures:
        requirement = SUITE_REQUIREMENTS.get(failure.code)
        if requirement is not None:
            failing[requirement].add(failure.path)
    return DeclarationTally(
        strata=tuple(tallies),
        requirements=tuple((name, tuple(sorted(failing[name]))) for name in ALL_REQUIREMENTS),
        outside_strata=outside_strata(root, strata if registered is None else registered),
    )


def outside_strata(root: Path, strata: Sequence[str]) -> tuple[str, ...]:
    """Test files under ``root`` that are not directly inside a registered stratum."""
    registered = {(root / stratum).resolve() for stratum in strata}
    found = [
        path.relative_to(root).as_posix()
        for path in root.rglob("test_*.py")
        if path.parent.resolve() not in registered
        and not IGNORED_DIR_NAMES.intersection(path.relative_to(root).parts)
    ]
    return tuple(sorted(found))


def require_declared(stratum_dir: Path) -> bool:
    """True when the stratum's STRATUM.toml sets ``require_declared = true``."""
    manifest = stratum_dir / "STRATUM.toml"
    if not manifest.is_file():
        return False
    try:
        with manifest.open("rb") as handle:
            return tomllib.load(handle).get("require_declared") is True
    except tomllib.TOMLDecodeError:
        return False


def _stratum_tally(root: Path, stratum: str) -> StratumTally:
    directory = root / stratum
    if not directory.is_dir():
        return StratumTally(stratum, 0, (), (), False)
    files = sorted(path.name for path in directory.glob("test_*.py"))
    declared = {path.name for path in declared_test_files(directory)}
    return StratumTally(
        name=stratum,
        test_files=len(files),
        declared=tuple(name for name in files if name in declared),
        without_rack=tuple(name for name in files if name not in declared),
        require_declared=require_declared(directory),
    )
