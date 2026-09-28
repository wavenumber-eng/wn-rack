"""Run a native test that a RACK header lists for an implementation.

A header entry such as ``"rust": {"status": "implemented", "test":
"src/rs/core/tests/test_l0_004_parse.rs"}`` names a test in the
implementation's own language. Rack runs it as one row with the
language's own runner (``cargo test -p <package> --test <file stem>`` for
``.rs``) and records only pass or fail and the runner's output. The native
test itself knows nothing about Rack.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from rack.declarations import ImplementationStatus, TestDeclaration

OUTPUT_TAIL_LINES = 60


class NativeTestFailure(Exception):
    """The native runner reported a failure; the message is its output."""


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
        command, package_dir = cargo_command(self.project_root, self.status.test)
        result = subprocess.run(
            command,
            cwd=package_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            output = (result.stdout + result.stderr).splitlines()
            raise NativeTestFailure(
                " ".join(command) + "\n" + "\n".join(output[-OUTPUT_TAIL_LINES:])
            )

    def repr_failure(
        self, excinfo: pytest.ExceptionInfo[BaseException], style: Any = None
    ) -> str | Any:
        if isinstance(excinfo.value, NativeTestFailure):
            return str(excinfo.value)
        return super().repr_failure(excinfo, style)

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.project_root / self.status.test, 0, self.name


def cargo_command(project_root: Path, test: str) -> tuple[list[str], Path]:
    """``cargo test`` for the integration test file ``test``, and the package directory.

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
    return ["cargo", "test", "-p", package, "--test", path.stem], manifest.parent


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
