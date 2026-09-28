"""Run a native test that a RACK header lists for an implementation.

A header entry such as ``"rust": {"status": "implemented", "test":
"src/rs/core/tests/test_l0_004_parse.rs"}`` names a test in the
implementation's own language. Rack runs it with the language's own runner
and records only pass or fail and the runner's output. The native test itself
knows nothing about Rack.

Rust tests are batched: the first row of a crate to run starts one
``cargo test --no-fail-fast -p <crate> --test <a> --test <b> ...`` for every
selected Rust test of that crate, and each row reads its own test binary's
section of the output. One cargo call per crate pays cargo's startup once and
lets it build the test binaries in parallel. Under pytest-xdist a worker does
not know which rows it will run, so each row runs its own cargo call.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from rack.declarations import ImplementationStatus, TestDeclaration

OUTPUT_TAIL_LINES = 60
# Cargo announces each test binary it runs: "Running tests\\test_x.rs (target\\...)".
_RUNNING = re.compile(r"^\s*Running (?:tests[\\/])?(?P<stem>[\w-]+)\.rs\b", re.MULTILINE)
_SECTION_END = re.compile(r"^\s*(?:Running |Doc-tests )", re.MULTILINE)


class NativeTestFailure(Exception):
    """The native runner reported a failure; the message is its output."""


@dataclass(frozen=True)
class CargoTarget:
    """One Rust integration test file: its crate and its test binary."""

    package: str
    package_dir: Path
    stem: str


@dataclass(frozen=True)
class TargetResult:
    passed: bool
    output: str


_BATCHES = pytest.StashKey[dict[tuple[str, str], dict[str, TargetResult]]]()


class CargoTest(pytest.Item):
    """``cargo test`` for one Rust integration test file."""

    def __init__(
        self,
        *,
        declaration: TestDeclaration,
        status: ImplementationStatus,
        project_root: Path,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.declaration = declaration
        self.status = status
        self.project_root = project_root
        # Read by status handling and reports: which header row this item is.
        self.rack_native_row = (declaration, status.name)

    def runtest(self) -> None:
        target = cargo_target(self.project_root, self.status.test)
        result = self._result(target)
        if not result.passed:
            raise NativeTestFailure(result.output)

    def _result(self, target: CargoTarget) -> TargetResult:
        if os.environ.get("PYTEST_XDIST_WORKER"):
            return run_cargo(target.package_dir, target.package, [target.stem])[target.stem]
        batches = self.config.stash.setdefault(_BATCHES, {})
        key = (str(target.package_dir), target.package)
        if key not in batches:
            batches[key] = run_cargo(target.package_dir, target.package, self._batch_stems(target))
        return batches[key][target.stem]

    def _batch_stems(self, target: CargoTarget) -> list[str]:
        """Every selected, unskipped Rust test of this crate in the session."""
        stems = {target.stem}
        for item in self.session.items:
            if isinstance(item, CargoTest) and item.get_closest_marker("skip") is None:
                other = cargo_target(item.project_root, item.status.test)
                if (other.package_dir, other.package) == (target.package_dir, target.package):
                    stems.add(other.stem)
        return sorted(stems)

    def repr_failure(
        self, excinfo: pytest.ExceptionInfo[BaseException], style: Any = None
    ) -> str | Any:
        if isinstance(excinfo.value, NativeTestFailure):
            return str(excinfo.value)
        return super().repr_failure(excinfo, style)

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.project_root / self.status.test, 0, self.name


def cargo_target(project_root: Path, test: str) -> CargoTarget:
    """The crate and test binary of the integration test file ``test``.

    Cargo runs from the package directory, so a standalone crate and a crate in
    a workspace both resolve.
    """
    path = project_root / test
    manifest = next((d / "Cargo.toml" for d in path.parents if (d / "Cargo.toml").is_file()), None)
    if manifest is None:
        raise NativeTestFailure(f"{test} is not inside a Cargo package")
    with manifest.open("rb") as handle:
        package = tomllib.load(handle).get("package", {}).get("name")
    if not isinstance(package, str):
        raise NativeTestFailure(f"{manifest} has no [package] name")
    return CargoTarget(package, manifest.parent, path.stem)


def run_cargo(package_dir: Path, package: str, stems: list[str]) -> dict[str, TargetResult]:
    """Run the named test binaries in one cargo call and split the result per binary."""
    command = ["cargo", "test", "--no-fail-fast", "-p", package]
    command += [argument for stem in stems for argument in ("--test", stem)]
    # One stream keeps cargo's "Running" lines in order with each binary's output.
    completed = subprocess.run(
        command,
        cwd=package_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    header = " ".join(command)
    sections = split_sections(completed.stdout)
    tail = "\n".join(completed.stdout.splitlines()[-OUTPUT_TAIL_LINES:])
    results: dict[str, TargetResult] = {}
    for stem in stems:
        section = sections.get(stem)
        if section is None:
            # The binary never ran, usually because the build failed.
            results[stem] = TargetResult(False, f"{header}\n{tail}")
        else:
            passed = "test result: ok." in section
            results[stem] = TargetResult(passed, f"{header}\n{section.strip()}")
    return results


def split_sections(output: str) -> dict[str, str]:
    """Each test binary's part of cargo's output, keyed by the test file stem."""
    sections: dict[str, str] = {}
    for match in _RUNNING.finditer(output):
        end = _SECTION_END.search(output, match.end())
        sections[match.group("stem")] = output[match.start() : end.start() if end else len(output)]
    return sections


def native_items(
    module: pytest.Collector, declaration: TestDeclaration, project_root: Path
) -> list[pytest.Item]:
    """One row per implementation whose header entry names a native test."""
    return [
        CargoTest.from_parent(
            module,
            name=f"{declaration.id}[{status.name}]",
            declaration=declaration,
            status=status,
            project_root=project_root,
        )
        for status in declaration.implementations
        if status.test.endswith(".rs") and status.status != "not_applicable"
    ]
