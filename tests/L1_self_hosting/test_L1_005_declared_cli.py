from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from rack.audit import audit_suite
from rack.declaration_tally import tally_declarations

ROOT = Path(__file__).resolve().parents[2]
SOURCE_SUITE = ROOT / "tests" / "fixtures" / "declared_suite"
UNITS = Path("L0_units")
TEST_L0_001 = UNITS / "test_L0_001_parse_duration.py"


def copy_suite(tmp_path: Path) -> Path:
    suite = tmp_path / "declared_suite"
    shutil.copytree(
        SOURCE_SUITE, suite, ignore=shutil.ignore_patterns("__pycache__", "rack_results")
    )
    return suite


def audit_codes(suite: Path) -> list[str]:
    report = audit_suite(suite, signoff_strata=("L0_units",))
    return sorted(failure.code for failure in report.failures)


def replace_in(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def rack(*args: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("RACK_LANE", "WN_RACK_LANE", "WN_TEST_LANE", "RACK_IMPL")
    }
    env["RACK_TESTS_DIR"] = str(SOURCE_SUITE)
    return subprocess.run(
        [sys.executable, "-m", "rack", *args],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_audit_accepts_a_mixed_suite_without_entries_for_declared_files(tmp_path: Path) -> None:
    assert audit_codes(copy_suite(tmp_path)) == []


def test_audit_tallies_declared_files_requirements_and_outliers(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    (suite / "test_L0_099_loose.py").write_text("def test_loose():\n    pass\n", encoding="utf-8")
    nested = suite / UNITS / "extra" / "test_L0_098_nested.py"
    nested.parent.mkdir()
    nested.write_text("def test_nested():\n    pass\n", encoding="utf-8")
    replace_in(
        suite / TEST_L0_001,
        '"operations": ["ParseDuration"]',
        '"operations": ["ParseDuration", "FormatDuration"]',
    )
    replace_in(
        suite / UNITS / "test_L0_003_format_duration.py",
        '"checks": "Formatting seconds matches the captured reference text."',
        '"checks": "TODO"',
    )

    tally = audit_suite(suite, signoff_strata=("L0_units",)).declarations
    targeted = audit_suite(suite, signoff_strata=("L0_units",), target_stratum="L0_units")

    assert tally is not None
    assert (tally.test_files, tally.self_declared, tally.without_rack) == (5, 4, 1)
    assert tally.strata[0].without_rack == ("test_L0_004_legacy.py",)
    assert tally.outside_strata == (
        "L0_units/extra/test_L0_098_nested.py",
        "test_L0_099_loose.py",
    )
    assert targeted.declarations is not None
    assert targeted.declarations.outside_strata == tally.outside_strata
    assert tally_declarations(suite, [], [], registered=["L0_units"]).outside_strata == (
        tally.outside_strata
    )
    failing = {name: files for name, files in tally.requirements if files}
    assert failing == {
        "purpose": ("L0_units/test_L0_003_format_duration.py",),
        "trace": ("L0_units/test_L0_001_parse_duration.py",),
    }


def test_require_declared_fails_remaining_legacy_files(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    replace_in(
        suite / UNITS / "STRATUM.toml",
        "parallel = true\n",
        "parallel = true\nrequire_declared = true\n",
    )

    report = audit_suite(suite, signoff_strata=("L0_units",))

    assert [(f.code, f.subtest) for f in report.failures] == [
        ("undeclared_test_file", "test_L0_004_legacy.py")
    ]
    assert report.declarations is not None and report.declarations.strata[0].require_declared


def add_manifest_entry(suite: Path) -> None:
    with (suite / UNITS / "STRATUM.toml").open("a", encoding="utf-8") as handle:
        handle.write('\n[[subtests]]\nid = "L0_001"\nfile = "test_L0_001_parse_duration.py"\n')


def break_declaration(suite: Path) -> None:
    replace_in(suite / TEST_L0_001, '"comparator": "exact"', '"compare": "exact"')


def break_vectors(suite: Path) -> None:
    replace_in(
        suite / UNITS / "vectors" / "L0_001_parse_duration.json", '"fractional_hours"', '"x"'
    )


def break_trace(suite: Path) -> None:
    replace_in(
        suite / TEST_L0_001, '"operations": ["ParseDuration"]', '"operations": ["FormatDuration"]'
    )


def duplicate_id(suite: Path) -> None:
    replace_in(suite / UNITS / "STRATUM.toml", 'id = "L0_004"', 'id = "L0_001"')


def helper_imports_test(suite: Path) -> None:
    (suite / UNITS / "helpers.py").write_text(
        "from test_L0_001_parse_duration import RACK\n", encoding="utf-8"
    )


def declared_imports_test(suite: Path) -> None:
    replace_in(
        suite / TEST_L0_001,
        "\n\ndef run(case, impl):",
        "\n\nimport test_L0_004_legacy as _legacy\n\n\ndef run(case, impl):",
    )


@pytest.mark.parametrize(
    ("mutate", "codes"),
    [
        (add_manifest_entry, ["declared_file_in_manifest"]),
        (break_declaration, ["invalid_declaration"]),
        (break_vectors, ["invalid_cases"]),
        (break_trace, ["untraced", "untraced"]),
        (duplicate_id, ["duplicate_test_id"]),
        (helper_imports_test, ["test_module_import"]),
        (declared_imports_test, ["test_module_import"]),
    ],
)
def test_audit_rules_for_declared_files(
    tmp_path: Path, mutate: Callable[[Path], None], codes: list[str]
) -> None:
    suite = copy_suite(tmp_path)
    mutate(suite)

    assert audit_codes(suite) == codes


def test_cli_commands_read_declared_files() -> None:
    results = SOURCE_SUITE / "rack_results"
    if results.exists():
        shutil.rmtree(results)

    run = rack("run", "--all", "--impl", "shadow", "--lane", "full")
    assert run.returncode == 0, run.stdout + run.stderr
    assert "IMPLEMENTATIONS: shadow" in run.stdout

    subtest = json.loads(
        (results / "subtests" / "test_L0_001_parse_duration.json").read_text(encoding="utf-8")
    )
    rows = {test["name"]: test["rack"] for test in subtest["tests"]}
    assert rows["L0_001[long_form-shadow]"]["outcome"] == "pass"
    assert rows["L0_001[fractional_hours-shadow]"]["outcome"] == "deferred"
    assert rows["L0_001[fractional_hours-python]"]["detail"] == "implementation not selected"
    assert subtest["status"] == "passed"

    listed = rack("list", "L0_units", "--concern", "fixture")
    assert listed.returncode == 0, listed.stdout + listed.stderr
    for name in ("test_L0_001_parse_duration.py", "test_L0_002_vector_provenance.py"):
        assert name in listed.stdout

    for command in (("refresh",), ("report",), ("inventory",), ("status",)):
        completed = rack(*command)
        assert completed.returncode == 0, (command, completed.stdout + completed.stderr)

    refreshed = json.loads((results / "strata" / "L0_units.json").read_text(encoding="utf-8"))
    (declared,) = [s for s in refreshed["subtests"] if s["file"] == "test_L0_001_parse_duration.py"]
    assert all("rack" in test for test in declared["tests"])
    report_html = (results / "report.html").read_text(encoding="utf-8")
    assert "PARITY" in report_html and "Duration parsing" in report_html

    audited = rack("audit", "--signoff-stratum", "L0_units")
    assert audited.returncode == 0, audited.stdout + audited.stderr


def test_audit_holds_native_tests_to_the_naming_convention(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    source = (ROOT / "tests" / "L1_self_hosting" / "test_L1_004_plugin.py").read_text("utf-8")
    start = source.index('NATIVE_FORM_TEST = """') + len('NATIVE_FORM_TEST = """')
    text = source[start : source.index('"""', start)]
    plain = suite / UNITS / "test_L0_007_plain_parse.py"
    plain.write_text(text, encoding="utf-8")

    assert audit_codes(suite) == []

    renamed = suite / "rust_durations" / "tests" / "test_l0_007_parse.rs"
    (suite / "rust_durations" / "tests" / "test_l0_007_plain_parse.rs").rename(renamed)
    replace_in(plain, "test_l0_007_plain_parse.rs", "test_l0_007_parse.rs")
    replace_in(renamed, "fn l0_007_plain_parse", "fn parses")
    report = audit_suite(suite, signoff_strata=("L0_units",))

    messages = [failure.message for failure in report.failures if failure.code == "native_test"]
    assert any("must be named test_l0_007_plain_parse.rs" in m for m in messages)
    assert any("must define fn l0_007_plain_parse" in m for m in messages)
    assert not any("does not read L0_001_parse_duration.json" in m for m in messages)

    # An implemented native test must read the vector file; a suspended one may
    # predate it.
    replace_in(renamed, "L0_001_parse_duration.json", "another_file.json")
    report = audit_suite(suite, signoff_strata=("L0_units",))
    assert any("does not read L0_001_parse_duration.json" in f.message for f in report.failures)
    replace_in(
        plain,
        '"status": "implemented",\n            "test"',
        '"status": "suspended",\n            "reason": "port paused",\n            "test"',
    )
    report = audit_suite(suite, signoff_strata=("L0_units",))
    assert not any("does not read" in f.message for f in report.failures)


def test_audit_resolves_code_listed_in_the_header(tmp_path: Path) -> None:
    suite = copy_suite(tmp_path)
    source = (ROOT / "tests" / "L1_self_hosting" / "test_L1_004_plugin.py").read_text("utf-8")
    start = source.index('NATIVE_FORM_TEST = """') + len('NATIVE_FORM_TEST = """')
    text = source[start : source.index('"""', start)].replace(
        '"python": {"status": "implemented"},',
        '"python": {"status": "implemented", "code": [{"file": "suite_support/durations.py", '
        '"module": "suite_support.durations", "function": "parse_duration"}]},',
    )
    plain = suite / UNITS / "test_L0_007_plain_parse.py"
    plain.write_text(text, encoding="utf-8")

    assert audit_codes(suite) == []

    replace_in(plain, '"function": "parse_duration"', '"function": "parse_hours"')
    assert audit_codes(suite) == ["untraced"]
