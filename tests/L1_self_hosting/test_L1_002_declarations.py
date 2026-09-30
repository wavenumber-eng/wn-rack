from __future__ import annotations

import json
from pathlib import Path

import pytest

from rack.code_refs import check_code_ref, locate_code_ref
from rack.declarations import (
    CodeRef,
    DeclarationError,
    declaration_problems,
    is_self_declared,
    load_vector_file,
    manifest_entry,
    read_declaration,
)

VALID = """RACK = {
    "id": "L1_012",
    "title": "Duration parsing",
    "purpose": {
        "checks": "Duration strings parse to whole seconds.",
        "because": "Schedules built on a wrong parse miss every deadline.",
    },
    "concerns": ["time.parse"],
    "resources": ["vectors/L1_012_parse_duration.json"],
    "implementations": {
        "python": {"status": "implemented"},
        "rust": {"status": "planned", "reason": "not ported yet", "issue": "#41"},
        "cpp": {"status": "suspended", "reason": "C++ port paused by policy"},
        "wasm": {"status": "not_applicable", "reason": "no WASM target"},
    },
}


def _text(case):
    return case["inputs"]["text"]


def test_parse_duration():
    assert _text({"inputs": {"text": "1h"}}) == "1h"
"""


def write_test(tmp_path: Path, source: str, name: str = "test_L1_012_parse_duration.py") -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def test_valid_declaration_reads_statuses(tmp_path: Path) -> None:
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
    assert declaration.resources == ("vectors/L1_012_parse_duration.json",)
    assert declaration.checks == "Duration strings parse to whole seconds."
    assert declaration.own is not None and declaration.own.name == "python"
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
        (
            "def test_parse_duration():",
            "def test_other():\n    pass\n\n\ndef test_parse_duration():",
            "exactly one test_",
        ),
        (
            "def test_parse_duration():\n",
            "def run(case, impl):\n    return None\n\n\ndef _unused():\n",
            r"the run\(case, impl\) form was removed",
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
        (
            '"python": {"status": "implemented"}',
            '"python": {"status": "implemented", "cod": []}',
            "unknown keys",
        ),
        (
            '"python": {"status": "implemented"}',
            '"python": {"status": "implemented", "code": [{"file": "a.py"}]}',
            "code entries need exactly file, module, and function",
        ),
        ('"resources": [', '"operations": ["Parse"],\n    "resources": [', "removed run"),
        ('"resources": [', '"cases": {"file": "v.json"},\n    "resources": [', "removed run"),
        ('"resources": [', '"expect": {"source": "contract"},\n    "resources": [', "removed"),
        ('"resources": [', '"deferred": {},\n    "resources": [', "removed run"),
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
    "implementations": {"python": {"status": "suspended", "reason": "not a check"}},
}


def test_manifest_lists_every_file():
    assert True
"""
    with pytest.raises(DeclarationError, match="check declares no implementations"):
        read_declaration(write_test(tmp_path, source, "test_L99_001_manifest.py"))
    valid = source.replace(
        '    "implementations": {"python": {"status": "suspended", "reason": "not a check"}},\n',
        "",
    )
    assert read_declaration(write_test(tmp_path, valid, "test_L99_001_manifest.py")).kind == "check"


def write_vectors(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "vectors.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def vector_payload() -> dict[str, object]:
    return {
        "schema": "rack.cases.a0",
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


def test_legacy_file_is_not_self_declared(tmp_path: Path) -> None:
    legacy = write_test(tmp_path, "def test_something():\n    assert True\n", "test_L1_001_x.py")
    assert not is_self_declared(legacy)


def test_every_failing_requirement_is_reported_on_its_own(tmp_path: Path) -> None:
    source = (
        VALID.replace('"checks": "Duration strings parse to whole seconds."', '"checks": ""')
        .replace('"concerns": [', '"expect": {},\n    "concerns": [')
        .replace('"issue": "#41"', '"issue": ""')
    )

    problems = declaration_problems(write_test(tmp_path, source))

    assert sorted(problems) == ["implementations", "keys", "purpose"]
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
    cpp = tmp_path / "src" / "cpp" / "src" / "durations.cpp"
    cpp.parent.mkdir(parents=True)
    cpp.write_text(CPP_MODULE, encoding="utf-8")
    return tmp_path


CPP_MODULE = """#include "durations.h"

namespace clockwork::durations
{
namespace
{
int helper() { return 0; }
} // namespace

std::uint64_t parse_duration(std::string_view text)
{
    return static_cast<std::uint64_t>(helper() + to_seconds(text));
}

void Parser::parse()
{
    const auto seconds = parse_duration("1h");
}

// int commented_out(int x) { return x; }
/* int block_commented(int x) { return x; } */
} // namespace clockwork::durations
"""


PY = "src/py/clockwork/durations.py"
CPP = "src/cpp/src/durations.cpp"
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
        (CodeRef(CPP, "clockwork::durations", "parse_duration"), None),
        (CodeRef(CPP, "clockwork::durations", "Parser::parse"), None),
        (CodeRef(CPP, "clockwork::timing", "parse_duration"), "does not open namespace"),
        (CodeRef(CPP, "clockwork::durations", "seconds_between"), "defines no seconds_between"),
        (CodeRef(CPP, "clockwork::durations", "commented_out"), "defines no commented_out"),
        (CodeRef(CPP, "clockwork::durations", "to_seconds"), "defines no to_seconds"),
        (CodeRef(CPP, "clockwork::durations", "helper"), None),
        (CodeRef(CPP, "clockwork::durations", "block_commented"), "defines no block_commented"),
        (CodeRef(CPP, "clockwork::durations", "Timer::parse"), "defines no Timer::parse"),
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


def test_code_refs_report_the_definition_line(tmp_path: Path) -> None:
    project = write_project(tmp_path)

    assert locate_code_ref(project, CodeRef(PY, "clockwork.durations", "parse_duration")).line == 6
    assert locate_code_ref(project, CodeRef(RS, "clock_work::durations", "Parser::parse")).line == 4
    assert (
        locate_code_ref(project, CodeRef(CPP, "clockwork::durations", "Parser::parse")).line == 15
    )


PYTEST_FORM = """import pytest

RACK = {
    "id": "L1_012",
    "title": "Duration parsing",
    "purpose": {
        "checks": "Duration strings parse to whole seconds.",
        "because": "Schedules built on a wrong parse miss every deadline.",
    },
    "resources": ["vectors/L1_012_parse_duration.json"],
    "implementations": {
        "python": {"status": "implemented"},
        "cpp": {"status": "suspended", "reason": "C++ port paused by policy"},
    },
}


def python_parse(text):
    return 0


def cpp_parse(text):
    raise NotImplementedError("no C++ runner")


IMPLEMENTATIONS = {
    "python": python_parse,
    "cpp": cpp_parse,
}


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_parse_duration(implementation):
    assert IMPLEMENTATIONS[implementation]("0s") == 0
"""


def test_pytest_form_is_an_ordinary_test_with_a_header(tmp_path: Path) -> None:
    declaration = read_declaration(write_test(tmp_path, PYTEST_FORM))

    assert declaration.resources == ("vectors/L1_012_parse_duration.json",)
    assert [(s.name, s.status) for s in declaration.implementations] == [
        ("python", "implemented"),
        ("cpp", "suspended"),
    ]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            "def test_parse_duration",
            "def test_other():\n    pass\n\n\ndef test_parse_duration",
            "exactly one test_",
        ),
        (
            '"resources": [',
            '"operations": ["Parse"],\n    "resources": [',
            "removed run",
        ),
        ('"resources": [', '"expect": {"source": "contract"},\n    "resources": [', "expect"),
        ('"resources": [', '"deferred": {},\n    "resources": [', "deferred"),
        (
            'assert IMPLEMENTATIONS[implementation]("0s") == 0',
            'assert IMPLEMENTATIONS[implementation]("0s") == IMPLEMENTATIONS["python"]("0s")',
            "uses IMPLEMENTATIONS other than",
        ),
        ("def test_parse_duration(implementation)", "def test_parse_duration(name)", "takes"),
        (
            '"resources": ["vectors/L1_012_parse_duration.json"]',
            '"resources": "vectors"',
            "resources must be a list",
        ),
        ('    "cpp": cpp_parse,\n', "", "must list the RACK implementations"),
        ("IMPLEMENTATIONS = {", "IMPLEMENTATIONS_BY_NAME = {", "top-level"),
    ],
)
def test_pytest_form_rules_fail_closed(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(DeclarationError, match=message):
        read_declaration(write_test(tmp_path, PYTEST_FORM.replace(old, new, 1)))


PLAIN_FORM = """RACK = {
    "id": "L1_012",
    "title": "Duration parsing",
    "purpose": {
        "checks": "Duration strings parse to whole seconds.",
        "because": "Schedules built on a wrong parse miss every deadline.",
    },
    "implementations": {
        "python": {"status": "implemented"},
        "cpp": {"status": "suspended", "reason": "C++ port paused by policy"},
    },
}


def run(text):
    return 0


def test_parse_duration():
    assert run("0s") == 0
"""


def test_plain_test_runs_the_first_implementation_without_a_native_test(tmp_path: Path) -> None:
    declaration = read_declaration(write_test(tmp_path, PLAIN_FORM))

    # A helper named run is an ordinary function in a test_* file.
    assert declaration.own is not None and declaration.own.name == "python"

    # A second implemented implementation needs its own row.
    implemented = PLAIN_FORM.replace(
        '{"status": "suspended", "reason": "C++ port paused by policy"}',
        '{"status": "implemented"}',
    )
    with pytest.raises(DeclarationError, match=r"implemented \['cpp'\] need a native test"):
        read_declaration(write_test(tmp_path, implemented))


def test_manifest_entry_reports_a_listed_class_as_a_class(tmp_path: Path) -> None:
    project = write_project(tmp_path)
    (project / "pyproject.toml").write_text("[project]\nname = 'clockwork'\n", encoding="utf-8")
    stratum = project / "tests" / "L1_time"
    stratum.mkdir(parents=True)
    source = VALID.replace(
        '"python": {"status": "implemented"},',
        '"python": {"status": "implemented", "code": ['
        f'{{"file": "{PY}", "module": "clockwork.durations", "function": "Parser"}}, '
        f'{{"file": "{PY}", "module": "clockwork.durations", "function": "parse_duration"}}]}},',
    )
    declaration = read_declaration(write_test(stratum, source))

    blocks = manifest_entry(declaration)["code_under_test"]

    assert blocks == [
        {"module": "clockwork.durations", "classes": ["Parser"], "functions": ["parse_duration"]}
    ]
