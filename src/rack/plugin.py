"""pytest plugin that runs self-declared Rack test files.

A self-declared file becomes one pytest item per (case, implementation). Every
selected implementation receives the same case; the file's ``run(case, impl)``
returns an observation, and Rack compares it with the case's expectation using
the declared comparator. Legacy test files are left to pytest unchanged.

Suite registrations (adapters, catalogs, comparators, loaders, services, lane
order) come from the nearest ``rack.toml`` above the test file. Registered
``module:attribute`` references resolve with that suite root on ``sys.path``.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from rack.declarations import (
    Case,
    DeclarationError,
    Difference,
    ImplementationStatus,
    TestDeclaration,
    is_self_declared,
    load_vector_file,
    read_declaration,
    validate_deferrals,
)
from rack.outcomes import (
    DEFERRED,
    ERROR,
    PASS,
    SKIPPED,
    canonical_digest,
    classify,
    compare_exact,
    difference_to_dict,
)

CHECK_IMPLEMENTATION = "check"
DEFAULT_LANES = ("fast", "full", "strict")
LANE_ENV_VARS = ("WN_RACK_LANE", "RACK_LANE", "WN_TEST_LANE")
_SUITES_KEY = pytest.StashKey[dict[Path, "Suite"]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("rack")
    group.addoption(
        "--rack-impl",
        default=None,
        help="Comma-separated implementations to run for self-declared tests",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "rack_implementation(name): implementation a self-declared row runs"
    )
    config.stash[_SUITES_KEY] = {}


def pytest_unconfigure(config: pytest.Config) -> None:
    for suite in config.stash.get(_SUITES_KEY, {}).values():
        suite.close()


def pytest_pycollect_makemodule(
    module_path: Path, parent: pytest.Collector
) -> pytest.Module | None:
    if module_path.name.startswith("test_") and is_self_declared(module_path):
        return RackFile.from_parent(parent, path=module_path)
    return None


class RackCase:
    """What ``run(case, impl)`` receives."""

    def __init__(
        self,
        case: Case,
        make_workdir: Callable[[], Path],
        suite: Suite,
    ) -> None:
        self.id = case.id
        self.inputs = case.inputs
        self._make_workdir = make_workdir
        self._workdir: Path | None = None
        self._suite = suite

    @property
    def workdir(self) -> Path:
        """A fresh directory for this row, created on first use."""
        if self._workdir is None:
            self._workdir = self._make_workdir()
        return self._workdir

    def service(self, name: str) -> object:
        return self._suite.service(name)


@dataclass
class Suite:
    """Registrations from one ``rack.toml`` plus session-scoped instances."""

    root: Path
    config: Mapping[str, object]
    _adapters: dict[str, object] = field(default_factory=dict)
    _services: dict[str, object] = field(default_factory=dict)
    timings: dict[tuple[str, str, str], float] = field(default_factory=dict)

    def lanes(self) -> tuple[str, ...]:
        table = _table(self.config, "lanes")
        order = table.get("order")
        if isinstance(order, list):
            return tuple(str(value) for value in order)
        named = tuple(str(key) for key, value in table.items() if isinstance(value, Mapping))
        return named or DEFAULT_LANES

    def adapter(self, name: str) -> object:
        if name not in self._adapters:
            entry = _table(_table(self.config, "implementations"), name)
            reference = entry.get("adapter")
            if not isinstance(reference, str):
                raise LookupError(f"implementation {name!r} has no adapter in rack.toml")
            self._adapters[name] = self._resolve(reference)()
        return self._adapters[name]

    def service(self, name: str) -> object:
        if name not in self._services:
            self._services[name] = self.registered("services", name)()
        return self._services[name]

    def registered(self, table: str, name: str) -> Callable[..., Any]:
        reference = _table(self.config, table).get(name)
        if not isinstance(reference, str):
            raise LookupError(f"{table} entry {name!r} is not registered in rack.toml")
        return self._resolve(reference)

    def close(self) -> None:
        for instance in (*self._adapters.values(), *self._services.values()):
            close = getattr(instance, "close", None)
            if callable(close):
                close()

    def _resolve(self, reference: str) -> Callable[..., Any]:
        module_name, _, attribute = reference.partition(":")
        if not attribute:
            raise LookupError(f"registration {reference!r} must be 'module:attribute'")
        if str(self.root) not in sys.path:
            sys.path.insert(0, str(self.root))
        return getattr(importlib.import_module(module_name), attribute)


class RackFile(pytest.Module):
    """A self-declared test file: one item per (case, implementation).

    The module is imported through pytest's configured import mode only when a
    row runs, so collection stays static.
    """

    def collect(self) -> Iterator[pytest.Item]:
        try:
            suite = _suite_for(self.config, self.path)
            declaration = read_declaration(self.path)
            cases = _load_cases(suite, declaration)
            validate_deferrals(declaration, tuple(case.id for case in cases))
            _check_lanes(suite, declaration, cases)
        except (DeclarationError, LookupError) as error:
            raise self.CollectError(str(error)) from error
        markers = _registered_markers(self.config)
        for case in cases:
            for status in _row_statuses(declaration):
                item = RackItem.from_parent(
                    self,
                    name=f"{declaration.id}[{case.id}-{status.name}]",
                    declaration=declaration,
                    case=case,
                    status=status,
                    suite=suite,
                )
                if status.name in markers:
                    item.add_marker(getattr(pytest.mark, status.name))
                yield item


class RackItem(pytest.Item):
    """One (case, implementation) row of a self-declared test."""

    def __init__(
        self,
        *,
        declaration: TestDeclaration,
        case: Case,
        status: ImplementationStatus,
        suite: Suite,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.declaration = declaration
        self.case = case
        self.status = status
        self.suite = suite
        self.add_marker(pytest.mark.rack_implementation(status.name))
        self._record(outcome="")

    def setup(self) -> None:
        reason = _skip_reason(self.config, self.suite, self.case, self.status)
        if reason is not None:
            outcome, detail = reason
            self._record(outcome=outcome, detail=detail)
            pytest.skip(f"{outcome}: {detail}")

    def runtest(self) -> None:
        try:
            context = self._context()
            observation, elapsed = self._observe(context)
            differences = self._differences(context, observation, elapsed)
        except Exception as error:
            self._record(outcome=ERROR, detail=f"{type(error).__name__}: {error}")
            if not self._gating():
                pytest.xfail(f"{self.status.status} {self.status.name}: {error}")
            raise
        deferred = self.declaration.deferred.get(self.status.name, {}).get(self.case.id)
        outcome, detail = classify(differences, deferred)
        self._record(outcome=outcome, detail=detail, differences=differences)
        self._report(outcome, detail, differences)

    def reportinfo(self) -> tuple[Path, int, str]:
        return self.path, 0, self.name

    def _context(self) -> RackCase:
        label = re.sub(r"[^\w.-]", "_", f"{self.declaration.id}-{self.case.id}-{self.status.name}")
        factory = self.config._tmp_path_factory  # type: ignore[attr-defined]
        return RackCase(self.case, lambda: factory.mktemp(label, numbered=True), self.suite)

    def _observe(self, context: RackCase) -> tuple[object, float]:
        module = self.parent.obj  # type: ignore[union-attr]
        impl = (
            None
            if self.status.name == CHECK_IMPLEMENTATION
            else self.suite.adapter(self.status.name)
        )
        start = time.perf_counter()
        observation = module.run(context, impl)
        elapsed = time.perf_counter() - start
        self.suite.timings[(self.declaration.id, self.case.id, self.status.name)] = elapsed
        return observation, elapsed

    def _differences(
        self, context: RackCase, observation: object, elapsed: float
    ) -> list[Difference]:
        expect = self.declaration.expect
        if expect.get("source") == "budget":
            return _budget_differences(
                self.suite, self.declaration, self.case, self.status.name, elapsed
            )
        expected = _expected(self.suite, self.declaration, self.case, context)
        if canonical_digest(expected) == canonical_digest(observation):
            return []
        name = str(expect.get("comparator", "exact"))
        comparator = (
            compare_exact if name == "exact" else self.suite.registered("comparators", name)
        )
        return list(comparator(expected, observation))

    def _gating(self) -> bool:
        # Planned and suspended rows run only on explicit selection and report
        # progress without failing the file.
        return self.status.status == "implemented"

    def _report(self, outcome: str, detail: str, differences: list[Difference]) -> None:
        if outcome == PASS:
            return
        if outcome == DEFERRED:
            pytest.xfail(f"deferred ({self._issue()}): {detail}")
        if not self._gating():
            pytest.xfail(f"{self.status.status} {self.status.name}: {detail}")
        lines = [detail] + [
            f"  {list(d.path)} {d.kind}: expected={d.expected!r} actual={d.actual!r}"
            for d in differences[:20]
        ]
        raise AssertionError("\n".join(lines))

    def _issue(self) -> str:
        return self.declaration.deferred_issues.get(self.status.name, {}).get(self.case.id, "")

    def _record(
        self,
        *,
        outcome: str,
        detail: str = "",
        differences: list[Difference] | None = None,
    ) -> None:
        self.user_properties[:] = [
            ("rack_test", self.declaration.id),
            ("rack_case", self.case.id),
            ("rack_implementation", self.status.name),
            ("rack_status", self.status.status),
            ("rack_outcome", outcome),
            ("rack_detail", detail),
            ("rack_differences", [difference_to_dict(d) for d in differences or []]),
        ]


def _row_statuses(declaration: TestDeclaration) -> tuple[ImplementationStatus, ...]:
    if declaration.kind == "check":
        return (ImplementationStatus(CHECK_IMPLEMENTATION, "implemented"),)
    return tuple(s for s in declaration.implementations if s.status != "not_applicable")


def _skip_reason(
    config: pytest.Config, suite: Suite, case: Case, status: ImplementationStatus
) -> tuple[str, str] | None:
    if status.name != CHECK_IMPLEMENTATION:
        selected = _selected_implementations(config)
        if selected is not None and status.name not in selected:
            return SKIPPED, "implementation not selected"
        if selected is None and status.status in ("planned", "suspended"):
            return status.status, status.reason
    lanes = suite.lanes()
    active = _active_lane(lanes)
    if active in lanes and lanes.index(case.lane) > lanes.index(active):
        return SKIPPED, f"lane {case.lane} is outside the active lane {active}"
    return None


def _active_lane(lanes: tuple[str, ...]) -> str:
    for name in LANE_ENV_VARS:
        value = os.environ.get(name, "").strip().lower()
        if value:
            return value
    return lanes[0]


def _selected_implementations(config: pytest.Config) -> set[str] | None:
    raw = config.getoption("rack_impl") or os.environ.get("RACK_IMPL", "")
    names = {name.strip() for name in str(raw).split(",") if name.strip()}
    return names or None


def _expected(suite: Suite, declaration: TestDeclaration, case: Case, context: RackCase) -> object:
    expect = declaration.expect
    if expect.get("source") == "authority":
        return suite.registered("loaders", str(expect["loader"]))(context)
    if declaration.kind == "check" and case.expect is None:
        return []
    return case.expect


def _budget_differences(
    suite: Suite,
    declaration: TestDeclaration,
    case: Case,
    implementation: str,
    elapsed: float,
) -> list[Difference]:
    expect = declaration.expect
    metric = str(expect.get("metric"))
    differences: list[Difference] = []
    limit = expect.get("max")
    if isinstance(limit, (int, float)) and elapsed > float(limit):
        differences.append(Difference((metric,), "value", float(limit), elapsed))
    ratio = expect.get("max_ratio")
    baseline_name = str(expect.get("relative_to"))
    # An unselected baseline skips only the ratio check; the absolute check still applies.
    baseline = suite.timings.get((declaration.id, case.id, baseline_name))
    if isinstance(ratio, (int, float)) and baseline and baseline_name != implementation:
        allowed = float(ratio) * baseline
        if elapsed > allowed:
            differences.append(Difference((metric, "ratio"), "value", allowed, elapsed))
    return differences


def _load_cases(suite: Suite, declaration: TestDeclaration) -> tuple[Case, ...]:
    reference = declaration.cases
    if "file" in reference:
        return load_vector_file(declaration.path.parent / str(reference["file"])).cases
    name = str(reference["catalog"])
    cases = tuple(suite.registered("catalogs", name)())
    if not all(isinstance(case, Case) for case in cases):
        raise DeclarationError(f"catalog {name!r} must return rack Case objects")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise DeclarationError(f"catalog {name!r} returned duplicate case ids")
    return cases


def _check_lanes(suite: Suite, declaration: TestDeclaration, cases: tuple[Case, ...]) -> None:
    lanes = suite.lanes()
    unknown = sorted({case.lane for case in cases} - set(lanes))
    if unknown:
        raise DeclarationError(
            f"{declaration.path.name}: case lanes {unknown} are not in the suite lanes {list(lanes)}"
        )


def _suite_for(config: pytest.Config, path: Path) -> Suite:
    manifest = _find_rack_toml(path)
    suites = config.stash[_SUITES_KEY]
    if manifest not in suites:
        with manifest.open("rb") as handle:
            suites[manifest] = Suite(manifest.parent, tomllib.load(handle))
    return suites[manifest]


def _find_rack_toml(path: Path) -> Path:
    for directory in path.resolve().parents:
        candidate = directory / "rack.toml"
        if candidate.is_file():
            return candidate
    raise LookupError(f"{path.name}: no rack.toml above this self-declared test")


def _registered_markers(config: pytest.Config) -> set[str]:
    return {
        str(line).split(":", 1)[0].split("(", 1)[0].strip() for line in config.getini("markers")
    }


def _table(data: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = data.get(key, {})
    return value if isinstance(value, Mapping) else {}
