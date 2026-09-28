# Self-declared tests, implementations, and parity

Status: draft (2026-09-27), rewritten after the owner chose plain tests with
native tests per language over Rack-driven adapters.

## Why

Some suites test more than one implementation of the same behavior: a primary
implementation and one or more ports. They need three things Rack did not
give them: to know, per test and per implementation, what is implemented and
what is not; to judge every implementation against the same reference; and to
keep that bookkeeping inside each test so parallel work does not conflict in
shared files.

## Principles

1. **Independent reference.** Every implementation is judged against the same
   independent reference (a captured authority, a specification value, or a
   declared property), never against another implementation. The only
   cross-implementation comparison is a performance budget.
2. **Isolation.** Each test lives in its own files. Nothing central grows with
   each test, so work in different strata merges without conflicts.
3. **Boring tests.** A test is explicit and readable top to bottom. Rack is not
   in the loop of what a test does or how it compares; it runs, organizes,
   reports, and audits.
4. **Native tests.** An implementation in another language is tested by a
   plain test in that language, run by that language's own runner, reading the
   same reference files. It knows nothing about Rack.

## A test

A test has an id (`L1_012`), a Python test file that holds its `RACK` header,
its reference files, and one native test per other implementation.

`tests/L1_time/test_L1_012_parse_duration.py`:

```python
import json
from pathlib import Path

import pytest
from clockwork.durations import parse_duration

RACK = {
    "id": "L1_012",
    "title": "Duration parsing",
    "purpose": {
        "checks": "Duration strings such as 1h30m parse to whole seconds.",
        "because": "Schedules built on a wrong parse miss every deadline.",
    },
    "concerns": ["time.parse"],
    "resources": ["vectors/L1_012_parse_duration.json"],
    "implementations": {
        "python": {
            "status": "implemented",
            "code": [{"file": "src/py/clockwork/durations.py",
                      "module": "clockwork.durations", "function": "parse_duration"}],
        },
        "rust": {
            "status": "implemented",
            "test": "src/rs/clockwork/tests/test_l1_012_parse_duration.rs",
            "code": [{"file": "src/rs/clockwork/src/durations.rs",
                      "module": "clockwork::durations", "function": "parse_duration"}],
        },
        "cpp": {"status": "suspended", "reason": "C++ port paused by project policy"},
    },
}

VECTOR_FILE = Path(__file__).parent / "vectors" / "L1_012_parse_duration.json"
CASES = json.loads(VECTOR_FILE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_parse_duration(case: dict) -> None:
    assert parse_duration(case["inputs"]["text"]) == case["expect"]["seconds"]
```

`src/rs/clockwork/tests/test_l1_012_parse_duration.rs` is a plain `cargo test`
that loads the same vector file, calls `clockwork::durations::parse_duration`,
and fails with a message listing the failing cases.

### Header rules

- `RACK` is one top-level assignment of a pure literal, read with
  `ast.literal_eval`. Unknown keys fail. The id matches `L<n>_<nnn>` (optional
  lowercase suffix), is unique in the suite, and starts the file name.
- `purpose.checks` and `purpose.because` are required, at least three words
  each: what the test verifies and what breaks if it fails.
- Each implementation has `status`: `implemented`, `planned` (needs `reason`
  and `issue`), `suspended` (needs `reason`), or `not_applicable` (needs
  `reason`). Optional `test` names the native test file; optional `code` lists
  the functions exercised (`file`, `module`, `function`; `Type.method` in
  Python, `Type::method` in Rust and C++).
- `resources` lists the files the test reads, relative to the test file.
- The file has exactly one `test_*` function. When the file itself exercises
  several implementations (none of them with a `test` entry), it maps each to
  its function in a top-level `IMPLEMENTATIONS = {...}` dict, in header order,
  and parametrizes over `implementation`.
- Nothing imports from a test file; shared helpers live in helper modules.
- Checks (`"kind": "check"`) exercise no implementation, such as manifest or
  hygiene checks, and declare no implementations.

### Reference files

Vector files (`schema: "rack.cases.a0"`) hold cases with `id`, `inputs`,
`expect`, and an optional `lane`, plus `provenance` (`authority`,
`specification`, or `generated` by a tool that runs no implementation under
test). Bytes are written as `{"base64": ...}` or as a documented text
encoding. Captured authority files (for example a reader's output for a
reference document) may be used directly; the test reads them explicitly.

## What Rack does

### Running

- Pytest collects the Python test file normally.
- For each row whose implementation is not implemented (planned, suspended,
  not applicable), Rack adds a skip with the reason, unless `--rack-impl`
  selects that implementation; a selected planned or suspended row that fails
  is reported as an expected failure and does not fail the run. A row for an
  implementation the header does not declare, or in a file whose header is
  invalid, errors.
- For each header entry with a `test` ending in `.rs`, Rack adds one row that
  runs the native test. Rust tests of one crate run in a single
  `cargo test --no-fail-fast -p <crate> --test <a> --test <b> ...` call per run,
  from the package directory; each row reads its own test binary's section of
  the output and records pass or fail with that output. Under pytest-xdist each
  row runs its own cargo call.
- Rows carry a marker named after their implementation, so a suite's existing
  language flags (`-m rust`, skip or only options) keep working.
- `rack run --jobs N` runs strata that set `parallel = true` with pytest-xdist
  (`--dist loadfile`).

### Reporting

Each row records its test id, case, implementation, status, and outcome in
pytest `user_properties`; subtest and stratum JSON carry them under `"rack"`.

- `rack parity` counts, per stratum or concern and per implementation, tests
  by status and cases by outcome, plus debt: files without a header, deferred
  or planned work, suspended implementations, failing rows, and audit
  findings. `--format json` follows `rack.parity_report` `a0`.
- `rack report` adds a PARITY section: the implementation grid, each test's
  purpose, code, and case outcomes, and the debt view.

### Auditing

`rack audit` reads files only; it imports and builds nothing.

- Header rules above, each reported on its own (`invalid_declaration`).
- Vector files: schema, provenance, unique case ids (`invalid_cases`).
- `code` entries resolve in their named files, for implemented and suspended
  implementations (`untraced`): Python by parsing the file, Rust by module
  path, crate, and `fn`, C++ by namespace and definition.
- Native tests exist and follow the naming convention derived from the id and
  the Python file's slug: `test_l0_004_<slug>.rs` defining
  `fn l0_004_<slug>`, `test_L0_004_<slug>.cpp`. An implemented one must read
  the test's vector files (`native_test`).
- Unique ids across the suite, no STRATUM entry for a declared file, and no
  imports of test modules involving a declared file.
- A tally: per stratum, files with and without a header; per rule, the files
  failing it; and test files outside any stratum's top level. A stratum that
  sets `require_declared = true` fails the audit for any file without a header.

The audit report follows `rack.audit_report` `a1`.

## Configuration

`rack.toml` keeps suite facts only: strata, lanes (`[lanes] order`), and
`[rack] project_root` when code paths are not relative to the nearest
`pyproject.toml`. `STRATUM.toml` keeps stratum facts: order, description,
default concerns, `parallel`, and `require_declared`. Legacy test files still
need `[[subtests]]` entries; declared files must not have one.

## Compatibility and migration

Legacy test files keep running unchanged, and every existing command (`list`,
`run`, `status`, `report`, `refresh`, `inventory`, `audit`, `new`, progress)
works on legacy and mixed suites. A suite converts one file at a time.

## Transitional pieces

The first version drove tests through `run(case, impl)` with suite-registered
adapters, comparators, loaders, catalogs, services, an operation registry,
and `rack trace`. They still work for files already written that way and are
removed once those files convert.

## Release

This work stays on the `altium-monkey` development branch, which is never
force-pushed because suites pin commits from it. The official release follows
completion and ships with a migration guide.

## Decisions (2026-09-27)

- Plain tests with a `RACK` header; one native test per other language, run by
  its own runner; Rack only runs, organizes, reports, and audits.
- Every implementation against an independent reference; performance budgets
  are the only cross-implementation comparison.
- Isolation first: no central file grows with each test.
- `purpose` is required; code and native tests are listed per implementation
  and verified statically.
- Rust tests are batched per crate; C++ execution is decided when it resumes.
