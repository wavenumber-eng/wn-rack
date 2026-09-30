from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUITE = ROOT / "tests" / "fixtures" / "declared_suite"
TEST_L0_001 = Path("L0_units") / "test_L0_001_parse_duration.py"
VECTORS_L0_001 = Path("L0_units") / "vectors" / "L0_001_parse_duration.json"

Rows = dict[str, tuple[str, str, str]]


@pytest.fixture(scope="module", autouse=True)
def shared_cargo_target(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """One cargo target directory per module, so the fixture crate's dependencies build once."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CARGO_TARGET_DIR", str(tmp_path_factory.mktemp("cargo_target")))
        yield


def copy_suite(tmp_path: Path) -> Path:
    suite = tmp_path / "declared_suite"
    # Fresh modification times: copies share one cargo target directory, and cargo
    # judges a local crate fresh by comparing source times with its last build.
    shutil.copytree(
        SOURCE_SUITE,
        suite,
        ignore=shutil.ignore_patterns("__pycache__"),
        copy_function=shutil.copy,
    )
    return suite


def run_suite(
    suite: Path, *args: str, lane: str = ""
) -> tuple[subprocess.CompletedProcess[str], Rows]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("RACK_LANE", "WN_RACK_LANE", "WN_TEST_LANE", "RACK_IMPL")
    }
    if lane:
        env["RACK_LANE"] = lane
    report = suite / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            ".",
            "-p",
            "no:cacheprovider",
            "-q",
            "-rs",
            "--json-report",
            f"--json-report-file={report}",
            *args,
        ],
        cwd=suite,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, read_rows(report)


def read_rows(report: Path) -> Rows:
    payload = json.loads(report.read_text(encoding="utf-8"))
    rows: Rows = {}
    for test in payload["tests"]:
        properties = {
            key: value for item in test.get("user_properties", []) for key, value in item.items()
        }
        name = test["nodeid"].split("::")[-1]
        rows[name] = (
            test["outcome"],
            properties.get("rack_outcome", ""),
            properties.get("rack_detail", ""),
        )
    return rows


def row_properties(suite: Path, name: str) -> dict[str, object]:
    """The Rack user properties the last run recorded for one row."""
    payload = json.loads((suite / "report.json").read_text(encoding="utf-8"))
    for test in payload["tests"]:
        if test["nodeid"].split("::")[-1] == name:
            return {key: value for item in test["user_properties"] for key, value in item.items()}
    raise KeyError(name)


def outcomes(rows: Rows) -> dict[str, tuple[str, str]]:
    return {name: (row[0], row[1]) for name, row in rows.items()}


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_rows_follow_header_statuses(tmp_path: Path) -> None:
    result, rows = run_suite(copy_suite(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    parsing = "test_duration_parsing"
    formatting = "test_duration_formatting"
    round_trip = "test_duration_round_trip"
    provenance = "test_vector_files_name_provenance"
    assert outcomes(rows) == {
        f"{parsing}[hours_and_minutes-python]": ("passed", "pass"),
        f"{parsing}[hours_and_minutes-shadow]": ("passed", "pass"),
        f"{parsing}[hours_and_minutes-rust]": ("skipped", "planned"),
        f"{parsing}[hours_and_minutes-cpp]": ("skipped", "suspended"),
        f"{parsing}[fractional_hours-python]": ("passed", "pass"),
        f"{parsing}[fractional_hours-shadow]": ("xfailed", "deferred"),
        f"{parsing}[fractional_hours-rust]": ("skipped", "planned"),
        f"{parsing}[fractional_hours-cpp]": ("skipped", "suspended"),
        f"{parsing}[long_form-python]": ("passed", "pass"),
        f"{parsing}[long_form-shadow]": ("passed", "pass"),
        f"{parsing}[long_form-rust]": ("skipped", "planned"),
        f"{parsing}[long_form-cpp]": ("skipped", "suspended"),
        f"{provenance}[L0_001_parse_duration]": ("passed", "pass"),
        f"{provenance}[L0_003_format_duration]": ("passed", "pass"),
        f"{formatting}[ninety_minutes-python]": ("passed", "pass"),
        f"{formatting}[ninety_minutes-shadow]": ("passed", "pass"),
        f"{formatting}[zero-python]": ("passed", "pass"),
        f"{formatting}[zero-shadow]": ("passed", "pass"),
        f"{round_trip}[ninety_minutes-python]": ("passed", "pass"),
        f"{round_trip}[ninety_minutes-shadow]": ("passed", "pass"),
        f"{round_trip}[zero-python]": ("passed", "pass"),
        f"{round_trip}[zero-shadow]": ("passed", "pass"),
        "test_legacy_style_still_runs": ("passed", ""),
    }
    assert rows[f"{parsing}[hours_and_minutes-rust]"][2] == "planned: not ported yet (#1)"


def test_suite_skip_markers_are_recorded_as_skipped_rows(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    (suite / "conftest.py").write_text(
        "import pytest\n\n\n"
        "def pytest_collection_modifyitems(config, items):\n"
        "    for item in items:\n"
        "        if item.name.endswith('-shadow]'):\n"
        "            item.add_marker(pytest.mark.skip(reason='shadow disabled by suite'))\n",
        encoding="utf-8",
    )

    result, rows = run_suite(suite)

    assert result.returncode == 0, result.stdout + result.stderr
    assert rows["test_duration_parsing[hours_and_minutes-shadow]"] == (
        "skipped",
        "skipped",
        "shadow disabled by suite",
    )
    skip_lines = [line for line in result.stdout.splitlines() if line.startswith("SKIPPED")]
    assert skip_lines and not any("plugin.py" in line for line in skip_lines)


def test_implementation_selection(tmp_path: Path) -> None:
    result, rows = run_suite(copy_suite(tmp_path), "--rack-impl", "shadow,rust")

    assert result.returncode == 0, result.stdout + result.stderr
    parsing = "test_duration_parsing"
    assert rows[f"{parsing}[long_form-shadow]"][:2] == ("passed", "pass")
    assert rows[f"{parsing}[long_form-python]"] == (
        "skipped",
        "skipped",
        "skipped: implementation not selected",
    )
    assert rows[f"{parsing}[long_form-cpp]"][:2] == ("skipped", "skipped")
    # A selected planned implementation that fails is reported without gating.
    assert rows[f"{parsing}[long_form-rust]"][:2] == ("xfailed", "planned")
    assert "NotImplementedError" in rows[f"{parsing}[long_form-rust]"][2]
    # A check tests no implementation, so selection never skips it.
    assert rows["test_vector_files_name_provenance[L0_001_parse_duration]"][:2] == (
        "passed",
        "pass",
    )


def test_known_failure_is_a_strict_xfail(tmp_path: Path) -> None:
    result, rows = run_suite(copy_suite(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert rows["test_duration_parsing[fractional_hours-shadow]"] == (
        "xfailed",
        "deferred",
        "#2: shadow truncates fractional amounts",
    )

    # Once the known failure passes, the strict xfail fails the run until the
    # entry is removed.
    suite = copy_suite(tmp_path / "second")
    replace_in(suite / VECTORS_L0_001, '"text": "1.5h"', '"text": "90m"')

    result, rows = run_suite(suite)

    assert result.returncode == 1
    assert rows["test_duration_parsing[fractional_hours-shadow]"][:2] == ("failed", "fail")


def test_a_failing_row_is_a_fail_row(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    replace_in(
        suite / VECTORS_L0_001, '"expect": {"seconds": 5400}}', '"expect": {"seconds": 5401}}'
    )

    result, rows = run_suite(suite)
    properties = row_properties(suite, "test_duration_parsing[hours_and_minutes-python]")

    assert result.returncode == 1
    assert rows["test_duration_parsing[hours_and_minutes-python]"][:2] == ("failed", "fail")
    # The test body's own assertion reports the difference; Rack adds none.
    assert properties["rack_differences"] == []
    assert "assert 5400 == 5401" in result.stdout
    assert "plugin.py" not in result.stdout


def test_legacy_file_mentioning_rack_text_is_not_captured(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    legacy = suite / "L0_units" / "test_L0_005_mentions_rack.py"
    legacy.write_text(
        'SOURCE = """\nRACK = {"id": "L0_099"}\n"""\n\n\n'
        "def test_mentions_rack() -> None:\n    assert SOURCE\n",
        encoding="utf-8",
    )

    result, rows = run_suite(suite)

    assert result.returncode == 0, result.stdout + result.stderr
    assert rows["test_mentions_rack"][:2] == ("passed", "")


PYTEST_FORM_TEST = """import json
from pathlib import Path

import pytest
from suite_support import durations

RACK = {
    "id": "L0_007",
    "title": "Duration parsing, plain pytest",
    "purpose": {
        "checks": "Parsing duration strings returns the whole seconds they spell.",
        "because": "Callers schedule work from these seconds; a wrong parse shifts every deadline.",
    },
    "resources": ["vectors/L0_001_parse_duration.json"],
    "implementations": {
        "python": {"status": "implemented"},
        "shadow": {"status": "implemented"},
        "rust": {"status": "suspended", "reason": "port paused by policy"},
    },
}

VECTORS = Path(__file__).parent / "vectors" / "L0_001_parse_duration.json"
CASES = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]


def rust_parse_duration(text):
    raise NotImplementedError("no Rust port")


IMPLEMENTATIONS = {
    "python": durations.parse_duration,
    "shadow": durations.parse_duration_shadow,
    "rust": rust_parse_duration,
}


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_parse_duration(case, implementation):
    seconds = IMPLEMENTATIONS[implementation](case["inputs"]["text"])
    assert seconds == case["expect"]["seconds"]
"""


def test_a_plain_file_with_an_implementations_table(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    (suite / "L0_units" / "test_L0_007_plain_parse.py").write_text(
        PYTEST_FORM_TEST, encoding="utf-8"
    )

    result, rows = run_suite(suite)

    # Plain pytest ids and plain assert failures: the test body is the test's.
    assert rows["test_parse_duration[hours_and_minutes-shadow]"][:2] == ("passed", "pass")
    assert rows["test_parse_duration[fractional_hours-shadow]"][:2] == ("failed", "fail")
    assert "assert 3600 == 5400" in result.stdout
    # Rack only decides which rows run, from the RACK statuses.
    assert rows["test_parse_duration[hours_and_minutes-rust]"] == (
        "skipped",
        "suspended",
        "suspended: port paused by policy",
    )

    selected, rows = run_suite(suite, "--rack-impl", "rust")
    assert rows["test_parse_duration[hours_and_minutes-rust]"][:2] == ("xfailed", "suspended")
    assert rows["test_parse_duration[hours_and_minutes-python]"][:2] == ("skipped", "skipped")
    assert selected.returncode == 0, selected.stdout + selected.stderr

    plain = suite / "L0_units" / "test_L0_007_plain_parse.py"
    text = plain.read_text(encoding="utf-8")
    plain.write_text(
        text.replace(
            '    "rust": rust_parse_duration,\n',
            '    "rust": rust_parse_duration,\n    "ghost": rust_parse_duration,\n',
        ),
        encoding="utf-8",
    )
    invalid, rows = run_suite(suite)
    # The header no longer matches the table, so no row may run.
    assert rows["test_parse_duration[hours_and_minutes-python]"][0] == "error"
    assert "invalid RACK header" in invalid.stdout


NATIVE_FORM_TEST = """import json
from pathlib import Path

import pytest
from suite_support import durations

RACK = {
    "id": "L0_007",
    "title": "Duration parsing, each implementation in its own language",
    "purpose": {
        "checks": "Parsing duration strings returns the whole seconds they spell.",
        "because": "Callers schedule work from these seconds; a wrong parse shifts every deadline.",
    },
    "resources": ["vectors/L0_001_parse_duration.json"],
    "implementations": {
        "python": {"status": "implemented"},
        "rust": {
            "status": "implemented",
            "test": "rust_durations/tests/test_l0_007_plain_parse.rs",
        },
    },
}

VECTORS = Path(__file__).parent / "vectors" / "L0_001_parse_duration.json"
CASES = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_plain_parse(case):
    assert durations.parse_duration(case["inputs"]["text"]) == case["expect"]["seconds"]
"""


@pytest.mark.skipif(shutil.which("cargo") is None, reason="needs a Rust toolchain")
def test_native_rust_test_is_one_cargo_row(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    plain = suite / "L0_units" / "test_L0_007_plain_parse.py"
    plain.write_text(NATIVE_FORM_TEST, encoding="utf-8")

    result, rows = run_suite(suite)

    assert result.returncode == 0, result.stdout + result.stderr
    assert rows["test_plain_parse[hours_and_minutes]"][:2] == ("passed", "pass")
    assert rows["L0_007[rust]"][:2] == ("passed", "pass")
    # A native row is recorded under its test file, one case for the whole test.
    assert row_properties(suite, "L0_007[rust]")["rack_case"] == "test_l0_007_plain_parse.rs"

    rust_test = suite / "rust_durations" / "tests" / "test_l0_007_plain_parse.rs"
    replace_in(rust_test, "parse_duration(input)", "parse_duration(input) + 1")
    failed, rows = run_suite(suite)
    assert rows["L0_007[rust]"][:2] == ("failed", "fail")
    assert (
        "cargo test --no-fail-fast -p rust-durations --test test_l0_007_plain_parse"
        in failed.stdout
    )
    assert "hours_and_minutes: expected 5400, got 5401" in failed.stdout

    replace_in(
        plain,
        '"status": "implemented",\n            "test"',
        '"status": "suspended",\n            "reason": "port paused",\n            "test"',
    )
    _, rows = run_suite(suite)
    assert rows["L0_007[rust]"] == ("skipped", "suspended", "suspended: port paused")


@pytest.mark.skipif(shutil.which("cargo") is None, reason="needs a Rust toolchain")
def test_native_row_passes_only_when_its_named_test_function_passes(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    (suite / "L0_units" / "test_L0_007_plain_parse.py").write_text(
        NATIVE_FORM_TEST, encoding="utf-8"
    )
    rust_test = suite / "rust_durations" / "tests" / "test_l0_007_plain_parse.rs"
    replace_in(rust_test, "fn l0_007_plain_parse()", "fn some_other_name()")

    result, rows = run_suite(suite)

    # Cargo reports "test result: ok." but not the test the convention names.
    assert rows["L0_007[rust]"][:2] == ("failed", "fail")
    assert "cargo did not report a passing test named l0_007_plain_parse" in result.stdout


SECOND_NATIVE_TEST = (
    NATIVE_FORM_TEST.replace('"id": "L0_007"', '"id": "L0_008"')
    .replace("test_l0_007_plain_parse.rs", "test_l0_008_plain_round_trip.rs")
    .replace("def test_plain_parse", "def test_plain_round_trip")
)


@pytest.mark.skipif(shutil.which("cargo") is None, reason="needs a Rust toolchain")
def test_rust_tests_of_one_crate_run_in_one_cargo_call(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    units = suite / "L0_units"
    (units / "test_L0_007_plain_parse.py").write_text(NATIVE_FORM_TEST, encoding="utf-8")
    (units / "test_L0_008_plain_round_trip.py").write_text(SECOND_NATIVE_TEST, encoding="utf-8")
    replace_in(
        suite / "rust_durations" / "tests" / "test_l0_008_plain_round_trip.rs",
        'format!("{seconds}s")',
        'format!("{seconds}m")',
    )

    result, rows = run_suite(suite)

    assert rows["L0_007[rust]"][:2] == ("passed", "pass")
    assert rows["L0_008[rust]"][:2] == ("failed", "fail")
    batch = (
        "cargo test --no-fail-fast -p rust-durations "
        "--test test_l0_007_plain_parse --test test_l0_008_plain_round_trip"
    )
    assert batch in result.stdout
    # The failing row's message is its own binary's section, not the other's.
    failure = result.stdout[result.stdout.index("L0_008[rust]") :]
    assert "hours_and_minutes: expected 5400, got 324000" in failure
    assert "test l0_007_plain_parse" not in failure


@pytest.mark.skipif(shutil.which("cargo") is None, reason="needs a Rust toolchain")
def test_a_broken_or_missing_rust_test_fails_only_its_own_row(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    units = suite / "L0_units"
    (units / "test_L0_007_plain_parse.py").write_text(NATIVE_FORM_TEST, encoding="utf-8")
    (units / "test_L0_008_plain_round_trip.py").write_text(SECOND_NATIVE_TEST, encoding="utf-8")
    second = suite / "rust_durations" / "tests" / "test_l0_008_plain_round_trip.rs"
    replace_in(second, "let mut failures", 'let broken: u8 = "not a number";\n    let mut failures')

    _, rows = run_suite(suite)

    # The batch fails to build, so each binary reruns alone.
    assert rows["L0_007[rust]"][:2] == ("passed", "pass")
    assert rows["L0_008[rust]"][:2] == ("failed", "fail")

    second.unlink()
    result, rows = run_suite(suite)

    assert rows["L0_007[rust]"][:2] == ("passed", "pass")
    assert rows["L0_008[rust]"][:2] == ("failed", "fail")
    assert "test_l0_008_plain_round_trip.rs does not exist" in result.stdout


def test_every_row_of_a_plain_test_with_an_invalid_header_errors(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    plain = suite / "L0_units" / "test_L0_007_plain_parse.py"
    plain.write_text(
        NATIVE_FORM_TEST.replace(
            '"checks": "Parsing duration strings returns the whole seconds they spell."',
            '"checks": "parses"',
        ),
        encoding="utf-8",
    )

    result, rows = run_suite(suite)

    assert rows["test_plain_parse[hours_and_minutes]"][0] == "error"
    assert "invalid RACK header" in result.stdout
    assert result.returncode == 1


CHECK_FORM_TEST = """import json
from pathlib import Path

RACK = {
    "id": "L0_009",
    "title": "Vector files state their provenance",
    "kind": "check",
    "purpose": {
        "checks": "Every vector file in this stratum names where its values came from.",
        "because": "A value without a source cannot be told apart from a copied output.",
    },
}


def test_vector_provenance():
    for path in sorted((Path(__file__).parent / "vectors").glob("*.json")):
        assert json.loads(path.read_text(encoding="utf-8"))["provenance"]["source"]
"""


def test_a_plain_check_records_a_check_row(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    (suite / "L0_units" / "test_L0_009_vector_check.py").write_text(
        CHECK_FORM_TEST, encoding="utf-8"
    )

    result, rows = run_suite(suite)

    assert result.returncode == 0, result.stdout + result.stderr
    assert rows["test_vector_provenance"][:2] == ("passed", "pass")
    assert row_properties(suite, "test_vector_provenance")["rack_implementation"] == "check"
