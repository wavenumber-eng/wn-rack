from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rack.declarations import DeclarationError, load_declaration, read_declaration
from rack.tracing import (
    RegistryError,
    load_registry,
    operations_constructed,
    trace_declaration,
    trace_problems,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUITE = ROOT / "tests" / "fixtures" / "declared_suite"
UNITS = Path("L0_units")
TEST_L0_001 = UNITS / "test_L0_001_parse_duration.py"
REGISTRY = Path("suite_support") / "operations.toml"


def copy_suite(tmp_path: Path) -> Path:
    suite = tmp_path / "declared_suite"
    shutil.copytree(
        SOURCE_SUITE, suite, ignore=shutil.ignore_patterns("__pycache__", "rack_results")
    )
    return suite


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def problems_after(suite: Path, path: Path, old: str, new: str) -> list[str]:
    replace_in(suite / path, old, new)
    return trace_problems(load_declaration(suite / TEST_L0_001))


def test_trace_shows_every_hop_with_file_and_line(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)

    trace = trace_declaration(load_declaration(suite / TEST_L0_001))

    assert trace is not None and trace.all_problems == []
    (operation,) = trace.operations
    assert operation.operation == "ParseDuration" and operation.result == "DurationResult"
    by_name = {entry.implementation: entry for entry in operation.implementations}
    python = [(hop.role, hop.file, hop.line, hop.name) for hop in by_name["python"].hops]
    assert python == [
        ("dispatch", "suite_support/adapters.py", 44, "ParseDuration -> _parse"),
        ("handler", "suite_support/adapters.py", 17, "suite_support.adapters._parse"),
        ("calls", "suite_support/durations.py", 11, "suite_support.durations.parse_duration"),
    ]
    # The shadow table is found below its own class, not in the reference table.
    assert by_name["shadow"].hops[0].line == 51
    assert (by_name["rust"].status, by_name["rust"].hops) == ("planned", ())
    assert trace.calls("shadow")[0].function == "parse_duration_shadow"


@pytest.mark.parametrize(
    ("path", "old", "new", "problem"),
    [
        (
            TEST_L0_001,
            '"operations": ["ParseDuration"]',
            '"operations": ["ParseDuration", "FormatDuration"]',
            "declares FormatDuration but run never constructs it",
        ),
        (
            TEST_L0_001,
            '"operations": ["ParseDuration"]',
            '"operations": ["ParseDuration", "SplitDuration"]',
            "operation SplitDuration is not in suite_support/operations.toml",
        ),
        (
            REGISTRY,
            'after = "class ShadowAdapter"',
            'after = "class MissingAdapter"',
            "shadow: suite_support/adapters.py has no line mapping ParseDuration -> _parse_shadow",
        ),
        (
            REGISTRY,
            'function = "_parse_shadow"',
            'function = "_parse_slowly"',
            "shadow: suite_support/adapters.py defines no _parse_slowly",
        ),
        (
            REGISTRY,
            "[operations.ParseDuration.shadow]",
            "[operations.ParseDuration.unused]",
            "shadow: no handler for ParseDuration in suite_support/operations.toml",
        ),
        (
            REGISTRY,
            'function = "parse_duration_shadow"',
            'function = "format_duration"',
            "shadow: handler _parse_shadow never calls format_duration",
        ),
    ],
)
def test_each_broken_hop_is_named(
    tmp_path: Path, path: Path, old: str, new: str, problem: str
) -> None:
    problems = problems_after(copy_suite(tmp_path), path, old, new)

    assert any(problem in found for found in problems), problems


def test_run_constructs_exactly_the_declared_operations(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)

    assert operations_constructed(suite / TEST_L0_001, ["ParseDuration", "FormatDuration"]) == {
        "ParseDuration"
    }
    problems = problems_after(
        suite,
        TEST_L0_001,
        "    return result\n",
        "    impl.batch([FormatDuration(seconds=1)])\n    return result\n",
    )
    assert "run constructs FormatDuration but RACK operations does not declare it" in problems


def test_suspended_calls_are_verified_and_planned_are_not(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    with (suite / REGISTRY).open("a", encoding="utf-8") as handle:
        handle.write(
            "\n[operations.ParseDuration.cpp]\n"
            'calls = [{ file = "cpp/durations.cpp", module = "clock", function = "parse" }]\n'
            "\n[operations.ParseDuration.rust]\n"
            'calls = [{ file = "rust/src/lib.rs", module = "clock", function = "parse" }]\n'
        )

    problems = trace_problems(load_declaration(suite / TEST_L0_001))

    assert problems == ["cpp: cpp/durations.cpp does not exist"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[dispatch]\npython = 3\n[operations]\n", "dispatch.python must be a file"),
        ('[dispatch]\n[operations.A.python]\ncalls = "x"\n', "calls must be a list"),
        (
            '[dispatch]\n[operations.A.python]\ncalls = [{ file = "a.py" }]\n',
            "need exactly file, module, and function",
        ),
    ],
)
def test_registry_rules_fail_closed(tmp_path: Path, text: str, message: str) -> None:
    (tmp_path / "operations.toml").write_text(text, encoding="utf-8")

    with pytest.raises(RegistryError, match=message):
        load_registry(tmp_path, "operations.toml")


def test_checks_send_no_operations(tmp_path: Path) -> None:
    source = (SOURCE_SUITE / UNITS / "test_L0_002_vector_provenance.py").read_text("utf-8")
    path = tmp_path / "test_L0_002_vector_provenance.py"
    path.write_text(
        source.replace('"kind": "check",', '"kind": "check",\n    "operations": ["X"],')
    )

    with pytest.raises(DeclarationError, match="operations belong only to run"):
        read_declaration(path)


def rack_trace(suite: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["RACK_TESTS_DIR"] = str(suite)
    return subprocess.run(
        [sys.executable, "-m", "rack", "trace", *args],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_trace_command_prints_the_chain(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)

    text = rack_trace(suite, "L0_001")
    assert text.returncode == 0, text.stdout + text.stderr
    assert "  operation ParseDuration  (DurationResult)" in text.stdout
    assert "dispatch suite_support/adapters.py:44" in text.stdout
    assert "calls    suite_support/durations.py:11" in text.stdout
    assert "rust    planned: not ported yet (#1)" in text.stdout
    assert text.stdout.rstrip().endswith("trace complete")

    stratum = rack_trace(suite, "L0", "--format", "json")
    assert stratum.returncode == 0, stratum.stdout + stratum.stderr
    assert [entry["id"] for entry in json.loads(stratum.stdout)] == ["L0_001", "L0_003", "L0_006"]

    replace_in(suite / REGISTRY, 'function = "_parse_shadow"', 'function = "_parse_slowly"')
    broken = rack_trace(suite, "L0_001")
    assert broken.returncode == 1
    assert "PROBLEM  shadow: suite_support/adapters.py defines no _parse_slowly" in broken.stdout

    assert rack_trace(suite, "L7_404").returncode == 1
