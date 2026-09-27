# Self-declared tests, implementations, and parity

Status: draft for owner review (2026-09-27), revised after independent review.

## Why

Rack was built so a suite could say what is tested, in what order, and why,
and report every result the same way. Rack reports and gates per test file,
and its traceability is declared (`code_under_test`) rather than instrumented.

Suites that test more than one implementation of the same behavior (a primary
implementation and one or more ports) outgrew that model in three ways:

- The declarations live in a separate `STRATUM.toml`, so every new fact about
  a test is a second edit in a shared file, and merges conflict there.
- Each language got its own tests. Tests for the same behavior drifted:
  different inputs, different assertions, and sometimes one implementation was
  judged against another instead of against an independent reference.
- Parity status moved into hand-maintained ledgers beside the tests, with their
  own validation, hashes, and slow rebuilds.

This design makes the test file the single source of truth. A test declares
what it covers, which cases it runs, where the expected result comes from, and
the status of every implementation. Rack runs each case for each selected
implementation through the same entry point and compares the result the same
way. Parity status is then a report over declarations and results.

## Principles

1. One test file is one test: one entry point with one intent. A second,
   different check is a second file.
2. The harness owns the cases. Every selected implementation receives exactly
   the same case inputs and cannot skip or rewrite a case. The entry point must
   not branch on which implementation it received.
3. An implementation returns an observation of a declared type. It never sees
   the expectation and never asserts. Rack compares the observation with the
   case's expectation using the declared comparator.
4. The expectation is independent of the implementations: a captured reference
   from an independent authority, a contract value with recorded provenance, or
   a declared property. Behavior is never judged by comparing one
   implementation with another; the only cross-implementation comparison is a
   performance budget.
5. Every implementation has a declared status for every test: implemented,
   planned (intended but not built yet, with an issue), suspended (the whole
   duty is paused by policy), or not applicable. Known differences in an
   implemented test are per-case deferrals that name the exact expected
   difference.
6. Declarations are static. Rack reads them without importing the test module.
   Together the test files form a distributed registry: each test carries its
   own entry, so adding or changing a test never edits a shared ledger.
7. Rack stays generic. It knows tests, cases, implementations, observations,
   comparators, lanes, and outcomes. The suite supplies implementation
   adapters, operation and observation types, comparators, reference loaders,
   case catalogs, and services.

## The test file

A self-declared test file has a docstring, a `RACK` declaration, and one entry
point, `run`. Example (a library with a Python reference and a Rust port):

```python
"""parse_duration converts "1h30m" style strings to seconds."""

RACK = {
    "id": "L1_012",
    "title": "Duration parsing",
    "concerns": ["time.parse"],
    "code_under_test": [
        {"module": "clockwork.durations", "functions": ["parse_duration"]},
    ],
    "cases": {"file": "vectors/L1_012_parse_duration.json"},
    "observation": "DurationResult",
    "expect": {"source": "contract", "comparator": "exact"},
    "implementations": {
        "python": "implemented",
        "rust": "implemented",
        "cpp": {"suspended": "C++ port paused by project policy"},
    },
    "deferred": {
        "rust": {
            "fractional_hours": {
                "issue": "#41",
                "differences": [
                    {"path": ["seconds"], "kind": "value",
                     "expected": 5400, "actual": 5399},
                ],
            },
        },
    },
}


def run(case, impl):
    (result,) = impl.batch([ParseDuration(text=case.inputs["text"])])
    return result
```

Rules Rack enforces:

- `RACK` is one top-level assignment of a pure literal, read with
  `ast.literal_eval`. Unknown keys fail validation.
- `id` follows `L<stratum>_<nnn>` (three digits, optional lowercase suffix),
  is unique in the suite, and matches the file name `test_<id>_<slug>.py`.
- `run(case, impl)` is the only public entry point. Small private helpers
  (`_name`) are allowed; `test_*` functions are not.
- `run` must not branch on the implementation (no use of `impl.name`).
- No file may import from a test file. Shared helpers live in helper modules
  that contain no tests. `rack audit` checks imports statically.

`STRATUM.toml` keeps stratum facts only (order, description, default concerns,
parallel opt-in). Subtest entries for self-declared files are rejected, so each
fact exists in one place. The declaration schema also carries the descriptive
fields Rack already reports: `objectives`, `approach`, `test_case_type`,
`progress`, and `runtime_profile`.

## The entry point contract

`case` provides:

- `case.id` and `case.inputs` (decoded from the vector file or catalog);
- `case.workdir`, a fresh directory for this row;
- `case.service(name)`, a suite-registered session service (for example a
  reader that asks an independent authority what a written file contains).

`impl` provides `impl.batch(operations)`, which runs a list of the suite's typed
operations and returns their typed results in order. Handles created in a batch
live only for that batch. A domain error (the implementation rejecting an
input) is a typed result and part of the observation. A transport, build,
crash, or timeout fault raises and becomes the `error` outcome.

Rows are pytest items, so conftest fixtures still apply to services.

## Cases and vector files

Cases come from:

- a JSON vector file next to the test (`vectors/<id>_<slug>.json`); or
- a named catalog registered by the suite, for inputs outside the repository.

Vector file shape:

```json
{
  "schema": "rack.cases.a0",
  "observation": "DurationResult",
  "provenance": {
    "kind": "specification",
    "source": "docs/durations.md section 2, hand-derived",
    "date": "2026-09-27"
  },
  "cases": [
    {"id": "hours_and_minutes", "lane": "fast",
     "inputs": {"text": "1h30m"}, "expect": {"seconds": 5400}},
    {"id": "fractional_hours", "lane": "full",
     "inputs": {"text": "1.5h"}, "expect": {"seconds": 5400}},
    {"id": "binary_payload", "lane": "fast",
     "inputs": {"blob": {"base64": "AAEC"}}, "expect": {"length": 3}}
  ]
}
```

Provenance is required. `kind` is `authority` (captured from an independent
tool; name the tool and version), `specification` (derived from a document or
by hand), or `generated` (by a script that does not run any implementation
under test). A contract value never comes from running an implementation under
test. Byte values use `{"base64": "..."}`.

Deferrals are declared in `RACK`, keyed by implementation and case id, and are
validated against the complete case list before lane filtering: a deferral for
an unknown case, a duplicate, or an empty catalog fails collection. A deferred
row passes as `deferred` only when the observed difference list equals the
declared list exactly, values included.

## Implementations and selection

The suite registers implementations and their adapters. Selection:

```text
rack run L1 --impl rust
rack run L1 --impl python,rust
```

- Without `--impl`, Rack runs every `implemented` implementation.
- `planned` and `suspended` implementations run only when selected
  explicitly, and their outcomes are reported without gating the file. This
  lets a port check its progress on planned tests before flipping them to
  `implemented`.
- `not_applicable` implementations never run.
- Each row carries a pytest marker named after its implementation. While a
  suite still has legacy tests, `--impl` and the suite's existing
  language flags combine as an intersection.

## Expectations and comparison

`expect.source` is one of:

| Source | Meaning |
| --- | --- |
| `authority` | A captured reference from an independent authority. The case locates it; `expect.loader` names the suite loader that reads it. |
| `contract` | A value in the case, with the vector file's provenance. |
| `property` | A relation computed from the case itself, such as save-then-reopen yielding the same observation. A property never stands alone as a writer's only evidence. |
| `budget` | Performance tests only (see below). |

Comparators are registered by the suite; `exact` is built in. A comparator
returns a list of differences:

```json
{"path": ["items", 3, "width"], "kind": "value", "expected": 21.35, "actual": 21.36}
```

`kind` is one of `missing`, `extra`, `type`, `value`, `length`, `nonfinite`.
An empty list is a pass. When both sides provide canonical bytes, Rack compares
digests first and runs the comparator only on a mismatch.

Performance budget shape:

```python
"expect": {
    "source": "budget",
    "metric": "wall_seconds",
    "max": 2.0,
    "max_ratio": 0.5,
    "relative_to": "python",
}
```

`max` is absolute; `max_ratio` compares with the same case run by
`relative_to`. If the baseline implementation is not selected, the ratio check
reports `skipped` and the absolute check still runs. Rack measures wall time
around `run`; memory for out-of-process implementations is measured by their
adapter.

## Outcomes

One row per (test, case, implementation):

| Outcome | When | pytest result |
| --- | --- | --- |
| `pass` | No differences | passed |
| `fail` | Differences, or a deferred row that now passes | failed |
| `deferred` | The observed differences equal the declared ones exactly | xfailed |
| `planned` | Planned implementation, not selected | skipped (reported) |
| `suspended` | Suspended implementation, not selected | skipped (reported) |
| `not_applicable` | Declared not applicable | not collected; reported from the declaration |
| `skipped` | Excluded by lane or selection | skipped (reported) |
| `error` | Adapter unavailable, reference missing, transport or harness fault | error |

Excluded rows are collected and skipped, not dropped, so every unexecuted row
is visible. A missing reference or an unavailable adapter is `error`, never a
skip. The Rack outcome and the difference list travel in the pytest item's
`user_properties`; a file fails if any row fails or errors.

## Lanes

Lane tiers are data on cases. `rack.toml` `[lanes] order = ["fast", "full",
"strict"]` defines nesting: a case tagged `full` runs in `full` and `strict`.
Rack marks rows outside the active lane as skipped. Tests never read the lane
from the environment.

## Checks without implementations

Some tests exercise no implementation: manifest consistency, workspace
hygiene, inventories. They are self-declared with `"kind": "check"`, no
`implementations`, and an observation of findings compared with an empty list
or a contract. A check without a clear purpose, or one that can be done another
way (for example by `rack audit`), is retired rather than converted.

## Verified outputs

For a writer, the observation is what the independent authority reads from the
file the implementation wrote. Reading can be expensive, so a suite may give a
loader a verification store keyed by the written file's digest:

- a hit reuses the stored reading; the file is byte-identical, so the
  authority's reading is unchanged;
- a miss runs the live reader when one is available and stores the result;
- a miss with no live reader in this environment is `error` ("needs
  verification"), resolved by a batched capture that fills the store.

The store records the authority's version with each reading, so a new
authority version can require re-verification.

## Suite configuration

```toml
[implementations.python]
adapter = "tests.common.adapters:PythonAdapter"

[implementations.rust]
adapter = "tests.common.adapters:RustRunnerAdapter"
parallel = true

[catalogs]
"corpus.pcb_track_pairs" = "tests.common.catalogs:pcb_track_pairs"

[comparators]
"text.extent" = "tests.common.comparators:text_extent"

[loaders]
"interop.schdoc" = "tests.common.oracles:read_schdoc_with_interop"

[services]
"interop" = "tests.common.services:interop_reader"

[observations]
module = "tests.common.generated_observations"

[lanes]
order = ["fast", "full", "strict"]
```

## Accounting

`rack parity` answers "where do we stand" in seconds. It reads declarations
only, so it needs no test run; when results exist it adds the latest outcomes.

```text
rack parity                      # whole suite, by stratum
rack parity L1 --by concern      # one stratum, by concern
rack parity --format json        # for scripts and dashboards
```

For each stratum or concern and each implementation it counts tests by status
(implemented, planned, suspended, not applicable) and, for implemented tests,
cases passing, failing, and deferred. The same numbers head the report page and
drill down to tests and cases.

## Reporting and the derived ledger

Rack's results JSON gains the rows. From declarations and rows Rack reports:

- the implementation grid per stratum and concern;
- declared coverage (`code_under_test`) per implementation;
- debt: legacy test files, missing or invalid declarations, imports of test
  modules, deferred cases, suspended implementations, and suite-counted legacy
  patterns (a suite may register an audit rule, for example one that counts
  legacy implementation-to-implementation comparisons).

`rack report` renders both in `report.html`: an implementation grid (tests
expandable to cases, one column per implementation, each cell the latest
outcome, deferrals and suspensions linked to their issue or reason) and a debt
view grouped by stratum and concern so patterns are visible at a glance.

A suite that keeps a traceability database builds it from this JSON. `rack
audit` reads declarations statically and reports structure debt without running
tests. Its `a0` report keeps its shape; self-declared files add failure codes
(`invalid_declaration`, `invalid_cases`, `declared_file_in_manifest`,
`duplicate_test_id`, `test_module_import`). `rack parity --format json` uses the
`rack.parity_report` `a0` contract.

## Compatibility and migration

Legacy test files (a `STRATUM.toml` subtest entry plus any number of pytest
functions) keep running unchanged. A suite converts one file or concern at a
time; converted files drop their `STRATUM.toml` entry. Nothing forces a
whole-suite conversion.

Every existing command keeps working on legacy and mixed suites: `list`,
`run`, `status`, `report`, `refresh`, `inventory`, `audit`, `new stratum`,
`new subtest`, and progress reporting. All of them read test metadata through
one declarations loader that merges `STRATUM.toml` entries and file
declarations, so concern filtering, source hashes, module validation,
inventory, and progress opt-in behave the same for both. Rack's self-hosting
tests cover legacy, self-declared, and mixed fixture suites, and each Rack
commit a suite pins is also checked by running that suite.

## Parallel runs

`rack run --jobs N` runs a stratum's selected rows in N workers (default 1)
using pytest-xdist. It is tried first on L0 and adopted where it shortens the
test cycle. Requirements:

- a stratum opts in through `STRATUM.toml` (`parallel = true`); an
  implementation that needs an exclusive resource declares `parallel = false`,
  and strata run serially while it is selected;
- Rack uses `--dist loadfile` so a file's rows, module fixtures, and budget
  baselines share one worker;
- the suite builds shared artifacts (for example a native runner) before
  workers start, or guards the build with a lock, because session fixtures run
  once per worker;
- per-worker adapters start once per worker;
- result files, progress events, and `RackOutput` are written per worker and
  merged by Rack.

## Release

Other suites use Rack, so this work stays on the `altium-monkey` development
branch until the whole model is complete. That branch is never force-pushed,
because suites pin commits from it. The official release follows completion and
ships with a migration guide: what stays the same for legacy suites, how to
convert one file, how to register adapters, comparators, loaders, catalogs, and
services, and how to read the new reports.

## Implementation notes

- A Rack pytest plugin, registered as a `pytest11` entry point, recognizes a
  self-declared file in `pytest_pycollect_makemodule`, reads `RACK` statically,
  and creates one item per (case, implementation) with node ids like
  `<path>::L1_012[hours_and_minutes-rust]`.
- New code lives in new modules (`declarations`, `harness`, `outcomes`,
  `plugin`, `parallel`), not in `cli.py`.
- Registrations come from the nearest `rack.toml` above the test file and
  resolve `module:attribute` with that suite root on `sys.path`.
- Lane order is `[lanes] order`; without it, the order of the `[lanes.<name>]`
  tables. A case lane outside that order fails collection.
- The test module is imported through pytest's import mode only when a row
  runs, so collection never imports test code.
- Check rows (`"kind": "check"`) run under every `--impl` selection.

## Decisions (2026-09-27)

- `RACK = {...}` literal at the top of the file; JSON vector files.
- Statuses: implemented, planned, suspended, not applicable; per-case
  deferrals with exact differences, including `unsupported_operation` when an
  implemented port cannot yet run one case.
- `rack parity` gives the accounting from declarations alone.
- Small private helpers in a test file; nothing imports from a test file.
- Behavior is always judged against an independent reference; performance
  budgets are the only cross-implementation comparison.
- Parallel runs are an opt-in `--jobs` option in the first version, using
  pytest-xdist.
- Checks without implementations are self-declared checks; anything without a
  clear purpose is retired.
- A suspended implementation (C++ here) does not gate; it runs on request.
- Writers are judged by the authority's reading of their output, with an
  optional verification store keyed by output digest.
- Development on the `altium-monkey` branch until the official release.

