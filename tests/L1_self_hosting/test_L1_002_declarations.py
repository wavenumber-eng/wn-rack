from __future__ import annotations

import json
from pathlib import Path

import pytest

from rack.code_refs import check_code_ref
from rack.declarations import (
    CodeRef,
    DeclarationError,
    declaration_problems,
    is_self_declared,
    load_vector_file,
    read_declaration,
    validate_deferrals,
)

VALID = """RACK = {
    "id": "L1_012",
    "title": "Duration parsing",
    "purpose": {
        "checks": "Duration strings parse to whole seconds.",
        "because": "Schedules built on a wrong parse miss every deadline.",
    },
    "concerns": ["time.parse"],
    "cases": {"file": "vectors/L1_012_parse_duration.json"},
    "observation": "DurationResult",
    "expect": {"source": "contract", "comparator": "exact"},
    "implementations": {
        "python": {
            "status": "implemented",
            "code": [
                {
                    "file": "src/clockwork/durations.py",
                    "module": "clockwork.durations",
                    "function": "parse_duration",
                },
            ],
        },
        "rust": {"status": "planned", "reason": "not ported yet", "issue": "#41"},
        "cpp": {"status": "suspended", "reason": "C++ port paused by policy"},
        "wasm": {"status": "not_applicable", "reason": "no WASM target"},
    },
    "deferred": {
        "python": {
            "fractional_hours": {
                "issue": "#42",
                "differences": [
                    {"path": ["seconds"], "kind": "value", "expected": 5400, "actual": 5399}
                ],
            }
        }
    },
}


def _text(case):
    return case.inputs["text"]


def run(case, impl):
    return impl.batch([_text(case)])
"""


def write_test(tmp_path: Path, source: str, name: str = "test_L1_012_parse_duration.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def test_valid_declaration_reads_statuses_and_deferrals(tmp_path: Path) -> None:
    declaration = read_declaration(write_test(tmp_path, VALID))

    assert declaration.id == "L1_012"
    assert declaration.kind == "test"
    assert declaration.concerns == ("time.parse",)
    assert [(s.name, s.status) for s in declaration.implementations] == [
        ("python", "implemented"),
        ("rust", "planned"),
        ("cpp", "suspended"),
        ("wasm", "not_applicable"),
    ]
    rust = declaration.status_of("rust")
    assert rust is not None and rust.issue == "#41"
    python = declaration.status_of("python")
    assert python is not None and python.code == (
        CodeRef("src/clockwork/durations.py", "clockwork.durations", "parse_duration"),
    )
    assert declaration.checks == "Duration strings parse to whole seconds."
    (difference,) = declaration.deferred["python"]["fractional_hours"]
    assert difference.path == ("seconds",) and difference.expected == 5400
    assert declaration.deferred_issues["python"]["fractional_hours"] == "#42"
    assert is_self_declared(write_test(tmp_path, VALID))


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('"purpose": {', '"purpose_text": "x", "old": {', "unknown RACK keys"),
        ('"checks": "Duration strings parse to whole seconds."', '"checks": ""', "purpose.checks"),
        ('"because": "Schedules', '"because": "TODO", "x": "Schedules', "purpose must be"),
        ('"title":', '"unknown_key": 1, "title":', "unknown RACK keys"),
        ('"id": "L1_012"', '"id": "L1-12"', "id must match"),
        ('"id": "L1_012"', '"id": "L1_013"', "file name must start"),
        ("def run(case, impl):", "def test_other():\n    pass\n\n\ndef run(case, impl):", "test_*"),
        (
            "def run(case, impl):",
            "def helper():\n    pass\n\n\ndef run(case, impl):",
            "only public",
        ),
        ("def run(case, impl):", "def run(case):", "must take exactly"),
        (
            "return impl.batch",
            "if impl.name == 'rust':\n        pass\n    return impl.batch",
            "branch",
        ),
        (
            '{"status": "planned", "reason": "not ported yet", "issue": "#41"}',
            '{"status": "planned", "reason": "not ported yet"}',
            "needs an issue",
        ),
        (
            '{"status": "suspended", "reason": "C++ port paused by policy"}',
            '"maybe"',
            "needs a status",
        ),
        ('"reason": "C++ port paused by policy"', '"why": "paused"', "unknown keys"),
        ('"reason": "no WASM target"', '"reason": " "', "needs a reason"),
        ('"function": "parse_duration",', "", "exactly file, module, and function"),
        (
            '"status": "implemented",\n            "code": [',
            '"status": "implemented",\n            "unused": [',
            "unknown keys",
        ),
        ('"comparator": "exact"', '"compare": "exact"', "comparator is required"),
        ('"source": "contract"', '"source": "authority"', "needs expect.loader"),
        ('{"file": "vectors/L1_012_parse_duration.json"}', '{"glob": "*.json"}', "cases must be"),
        ('"deferred": {\n        "python"', '"deferred": {\n        "rust"', "only to implemented"),
        ('"kind": "value"', '"kind": "wrong"', "known kind"),
        ("RACK = {", "RACK = dict(**{", "pure literal"),
    ],
)
def test_declaration_rules_fail_closed(tmp_path: Path, old: str, new: str, message: str) -> None:
    assert old in VALID
    source = VALID.replace(old, new, 1)
    if new.startswith("RACK = dict"):
        source = source.replace("\n}\n\n\ndef _text", "\n})\n\n\ndef _text", 1)
    with pytest.raises(DeclarationError, match=message):
        read_declaration(write_test(tmp_path, source))


def test_check_kind_declares_no_implementations(tmp_path: Path) -> None:
    source = """RACK = {
    "id": "L99_001",
    "kind": "check",
    "purpose": {
        "checks": "The manifest lists every test file.",
        "because": "An unlisted file never runs in signoff.",
    },
    "cases": {"catalog": "suite.manifest"},
    "implementations": {"python": {"status": "suspended", "reason": "not a check"}},
}


def run(case, impl):
    return []
"""
    with pytest.raises(DeclarationError, match="check declares no implementations"):
        read_declaration(write_test(tmp_path, source, "test_L99_001_manifest.py"))
    valid = source.replace(
        '    "implementations": {"python": {"status": "suspended", "reason": "not a check"}},\n',
        "",
    )
    assert read_declaration(write_test(tmp_path, valid, "test_L99_001_manifest.py")).kind == "check"


def test_budget_expectation_shape(tmp_path: Path) -> None:
    budget = VALID.replace(
        '"expect": {"source": "contract", "comparator": "exact"}',
        '"expect": {"source": "budget", "metric": "wall_seconds", "max_ratio": 0.5}',
    )
    with pytest.raises(DeclarationError, match="max_ratio needs relative_to"):
        read_declaration(write_test(tmp_path, budget))
    fixed = budget.replace('"max_ratio": 0.5', '"max_ratio": 0.5, "relative_to": "python"')
    assert read_declaration(write_test(tmp_path, fixed)).expect["source"] == "budget"


def write_vectors(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "vectors.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def vector_payload() -> dict[str, object]:
    return {
        "schema": "rack.cases.a0",
        "observation": "DurationResult",
        "provenance": {"kind": "specification", "source": "docs/durations.md, hand-derived"},
        "cases": [
            {"id": "hours_and_minutes", "inputs": {"text": "1h30m"}, "expect": {"seconds": 5400}},
            {"id": "blob", "lane": "full", "inputs": {"data": {"base64": "AAEC"}}, "expect": None},
        ],
    }


def test_vector_file_loads_cases_and_decodes_bytes(tmp_path: Path) -> None:
    vectors = load_vector_file(write_vectors(tmp_path, vector_payload()))

    assert [case.id for case in vectors.cases] == ["hours_and_minutes", "blob"]
    assert vectors.cases[0].lane == "fast"
    assert vectors.cases[1].lane == "full"
    assert vectors.cases[1].inputs["data"] == b"\x00\x01\x02"
    assert vectors.provenance["kind"] == "specification"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p.update(schema="other"), "schema must be"),
        (lambda p: p.pop("provenance"), "provenance needs"),
        (
            lambda p: p.update(provenance={"kind": "python_output", "source": "x"}),
            "provenance needs",
        ),
        (lambda p: p.update(cases=[]), "non-empty cases"),
        (lambda p: p["cases"].append({"id": "blob"}), "unique"),
    ],
)
def test_vector_file_rules_fail_closed(tmp_path: Path, mutate, message: str) -> None:
    payload = vector_payload()
    mutate(payload)
    with pytest.raises(DeclarationError, match=message):
        load_vector_file(write_vectors(tmp_path, payload))


def test_deferrals_are_checked_against_the_complete_case_list(tmp_path: Path) -> None:
    declaration = read_declaration(write_test(tmp_path, VALID))

    validate_deferrals(declaration, ("hours_and_minutes", "fractional_hours"))
    with pytest.raises(DeclarationError, match="unknown case 'fractional_hours'"):
        validate_deferrals(declaration, ("hours_and_minutes",))
    with pytest.raises(DeclarationError, match="case list is empty"):
        validate_deferrals(declaration, ())


def test_legacy_file_is_not_self_declared(tmp_path: Path) -> None:
    legacy = write_test(tmp_path, "def test_something():\n    assert True\n", "test_L1_001_x.py")
    assert not is_self_declared(legacy)


def test_every_failing_requirement_is_reported_on_its_own(tmp_path: Path) -> None:
    source = (
        VALID.replace('"checks": "Duration strings parse to whole seconds."', '"checks": ""')
        .replace('"comparator": "exact"', '"compare": "exact"')
        .replace('"issue": "#41"', '"issue": ""')
    )

    problems = declaration_problems(write_test(tmp_path, source))

    assert sorted(problems) == ["expect", "implementations", "purpose"]
    assert declaration_problems(write_test(tmp_path, VALID)) == {}
    assert list(declaration_problems(write_test(tmp_path, "RACK = [1]\n"))) == ["rack_literal"]


PYTHON_MODULE = """class Parser:
    def parse(self):
        pass


def parse_duration(text):
    return 0
"""

RUST_MODULE = """pub struct Parser;

impl Parser {
    pub fn parse(&self) {}
}

// fn commented_out() {}
pub fn parse_duration(text: &str) -> u64 {
    0
}
"""


def write_project(tmp_path: Path) -> Path:
    python = tmp_path / "src" / "py" / "clockwork" / "durations.py"
    python.parent.mkdir(parents=True)
    python.write_text(PYTHON_MODULE, encoding="utf-8")
    crate = tmp_path / "src" / "rs" / "clock-work"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text('[package]\nname = "clock-work"\n', encoding="utf-8")
    (crate / "src" / "durations.rs").write_text(RUST_MODULE, encoding="utf-8")
    (crate / "src" / "lib.rs").write_text("pub mod durations;\n", encoding="utf-8")
    return tmp_path


PY = "src/py/clockwork/durations.py"
RS = "src/rs/clock-work/src/durations.rs"


@pytest.mark.parametrize(
    ("ref", "problem"),
    [
        (CodeRef(PY, "clockwork.durations", "parse_duration"), None),
        (CodeRef(PY, "clockwork.durations", "Parser.parse"), None),
        (CodeRef(PY, "clockwork.durations", "parse_hours"), "defines no parse_hours"),
        (CodeRef(PY, "clockwork.durations", "Timer.parse"), "defines no class Timer"),
        (CodeRef(PY, "clockwork.timing", "parse_duration"), "is not module clockwork.timing"),
        (CodeRef("src/py/clockwork/missing.py", "clockwork.missing", "f"), "does not exist"),
        (CodeRef(RS, "clock_work::durations", "parse_duration"), None),
        (CodeRef(RS, "clock_work::durations", "Parser::parse"), None),
        (CodeRef(RS, "clock_work::durations", "commented_out"), "defines no fn commented_out"),
        (CodeRef(RS, "clock_work::durations", "Timer::parse"), "has no impl for Timer"),
        (CodeRef(RS, "clock_work::timing", "parse_duration"), "is not module clock_work::timing"),
        (CodeRef(RS, "other_crate::durations", "parse_duration"), "is not in crate other_crate"),
        (CodeRef("src/rs/clock-work/src/lib.rs", "clock_work", "parse_duration"), "defines no fn"),
        (CodeRef("src/rs/clock-work/Cargo.toml", "clock_work", "x"), "no static check for '.toml'"),
    ],
)
def test_code_refs_resolve_by_reading_the_declared_file(
    tmp_path: Path, ref: CodeRef, problem: str | None
) -> None:
    result = check_code_ref(write_project(tmp_path), ref)

    if problem is None:
        assert result is None
    else:
        assert result is not None and problem in result
