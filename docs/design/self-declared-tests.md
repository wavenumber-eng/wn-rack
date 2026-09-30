# Self-declared tests, implementations, and parity

Status: draft (2026-09-27), rewritten after the owner chose plain tests with
native tests per language over Rack-driven adapters, and revised after an
independent review of the design against the code. The transitional
`run(case, impl)` form was removed on 2026-09-30; `docs/migrating-to-plain-tests.md`
converts files written that way.

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
   cross-implementation comparison is a performance budget, which the test
   measures and asserts in its own body.
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

`src/rs/clockwork/tests/test_l1_012_parse_duration.rs` is a plain `cargo test`:
a `#[test] fn l1_012_parse_duration()` that loads the same vector file, calls
`clockwork::durations::parse_duration` for every case, and fails with a message
listing the failing cases. The C++ entry has no `test`: nothing runs it, and
it is counted as suspended.

### Header rules

- `RACK` is one top-level assignment of a pure literal, read with
  `ast.literal_eval`. Unknown keys fail. The id matches `L<n>_<nnn>` (optional
  lowercase suffix), is unique in the suite, and starts the file name.
- `purpose.checks` and `purpose.because` are required, at least three words
  each: what the test verifies and what breaks if it fails.
- Each implementation has `status`: `implemented`, `planned` (needs `reason`
  and `issue`), `suspended` (needs `reason`), or `not_applicable` (needs
  `reason`). Optional `test` names the native test file, relative to the
  project root; optional `code` lists the functions exercised (`file`,
  `module`, `function`; `Type.method` in Python, `Type::method` in Rust and
  C++).
- `resources` lists the files the test reads, relative to the test file.
  Files read through a suite's own helpers (such as a shared corpus outside
  the repository) are not listed.
- The file has exactly one `test_*` function. A file with only a top-level
  `run` is the removed `run(case, impl)` form and is rejected with a pointer to
  the migration guide; a helper named `run` beside a `test_*` function is an
  ordinary function.
- The keys of the removed form (`cases`, `observation`, `expect`,
  `operations`, `deferred`) are rejected: a plain test reads its own reference
  files and does its own comparison.
- The file's test runs the first implementation listed without a `test`. An
  implementation without a `test` listed after it gets no row and must not be
  implemented; it is only counted.
- When the file itself runs more than one implementation, a top-level
  `IMPLEMENTATIONS = {...}` dict maps every implementation without a `test`
  (not-applicable ones left out) to its function, in header order. The test
  takes an `implementation` parameter, parametrizes over the dict, and uses it
  only as `IMPLEMENTATIONS[implementation]`, so every row runs one
  implementation and no row compares one implementation with another.
- Nothing imports from a test file; shared helpers live in helper modules.
- Checks (`"kind": "check"`) exercise no implementation, such as manifest or
  hygiene checks, and declare no implementations.

### Reference files

Vector files (`schema: "rack.cases.a0"`) hold cases with `id`, `inputs`,
`expect`, and an optional `lane`, plus `provenance`: `{"kind": ..., "source":
...}` with kind `authority`, `specification`, or `generated` (by a tool that
runs no implementation under test) and a non-empty source. Bytes are written
as `{"base64": ...}` or as a documented text encoding. Any other JSON
reference file, such as a captured authority, carries the same top-level
`provenance`. A test reads its reference files itself.

A test that runs vector cases parametrizes over a parameter named `case` with
the case ids as pytest ids; Rack records each row under the case's `id`.

## What Rack does

### Running

- Pytest collects the Python test file normally.
- A row's implementation is its `implementation` parameter, a native test's
  entry, or else the implementation the file's test runs. A check's rows are
  `check` rows.
- Implemented rows run. Planned and suspended rows are skipped with their
  reason; not-applicable rows are skipped.
- `--rack-impl a,b` (also the `RACK_IMPL` environment variable, and
  `rack run --impl`) selects implementations: only their rows run, planned and
  suspended ones included, and every other row is skipped as not selected. A
  selected planned or suspended row that fails is reported as an expected
  failure and does not fail the run. Selection never skips a check's rows,
  since a check tests no implementation.
- A known failure is a strict `xfail` on its row, with the tracking issue in
  the reason. It is recorded as a `deferred` row with that reason, and the
  strict mark fails the run once the case passes, until the mark is removed.
- Every row of a file whose header is invalid errors, and so does a row for an
  implementation the header does not declare.
- For each header entry with a `test` ending in `.rs`, Rack adds one row that
  runs the native test. Rust tests of one crate run in a single
  `cargo test --no-fail-fast -p <crate> --test <a> --test <b> ...` call per run,
  from the package directory, for every selected Rust test whose file exists.
  Each row reads its own test binary's section of the output and passes only
  when cargo reports that the function the convention names (the file name
  without `test_`) passed. A missing file fails only its row; a binary that
  did not run (usually a build failure) reruns alone, so one broken file does
  not fail its neighbors. Under pytest-xdist each row runs its own cargo call.
- Rows carry a marker named after their implementation, so a suite's existing
  language flags (`-m rust`, skip or only options) keep working.
- A plain test chooses its own cases, including any lane-dependent ones.
- `rack run --jobs N` runs strata that set `parallel = true` with pytest-xdist
  (`--dist loadfile`).

### Reporting

Each row records its test id, case, implementation, status, and outcome in
pytest `user_properties`; subtest and stratum JSON carry them under `"rack"`.
A native row's case is its test file's name.

- `rack parity` counts, per stratum or concern and per implementation, tests
  by status and cases by outcome, plus debt: files without a header, planned
  work, suspended implementations, deferred rows (with their xfail reasons),
  failing rows, and audit findings. A test owes the cases of the vector files
  its header lists in `resources`; a test that lists none counts its latest
  rows. A native test counts as one case of its implementation.
  `--format json` follows `rack.parity_report` `a0`.
- `rack report` adds a PARITY section: the implementation grid, each test's
  purpose, the code each implementation lists, and case outcomes, and the debt
  view.

### Auditing

`rack audit` reads files only; it imports and builds nothing.

- Header rules above, each reported on its own (`invalid_declaration`).
- Resources (`invalid_resource`): each listed file exists and is named in the
  plain test's source outside its header.
- Reference files (`invalid_cases`): a vector file's schema, provenance, and
  unique case ids; any other JSON resource's provenance.
- `code` entries of implemented and suspended implementations resolve in their
  named files (`unresolved_code`): Python by parsing the file, Rust by module path,
  crate, and `fn`, C++ by namespace and definition. Planned entries are not
  checked.
- Native tests (`native_test`) follow the naming convention derived from the
  id and the Python file's slug: `test_l0_004_<slug>.rs` defining
  `#[test] fn l0_004_<slug>`, `test_L0_004_<slug>.cpp`. An implemented or
  suspended native test must exist; a planned one may not exist yet. An
  implemented one must name every JSON resource of the test in a string
  literal (comments do not count).
- Unique ids across the suite and no STRATUM entry for a declared file.
- Imports (`test_module_import`): a declared file imports no test module, and
  no file in the audited strata imports a declared file.
- A tally: per stratum, files with and without a header; per rule, the files
  failing it; and test files outside any stratum's top level. A stratum that
  sets `require_declared = true` fails the audit for any file without a header.

The audit report follows `rack.audit_report` `a1`.

### What the audit cannot see

The audit checks structure, not meaning. It cannot tell that a native test
compares its results with the reference rather than with another
implementation's output, or that a `generated` provenance really ran no
implementation under test. Review keeps those, and `purpose` gives the
reviewer the claim to check.

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
`rack new subtest` still creates a legacy file; a converted test starts from a
copy of an existing plain test.

## Removed transitional form

The first version drove tests through `run(case, impl)` with suite-registered
adapters, comparators, loaders, catalogs, services, an operation registry,
typed comparison outcomes, performance budgets, and `rack trace`. All of it
was removed on 2026-09-30, once the suites that used it had converted; Rack's
own fixture suite converted the same day. `docs/migrating-to-plain-tests.md`
shows how to convert a file.

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
