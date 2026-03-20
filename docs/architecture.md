# Architecture

This document describes how Rack is structured today as a package and how it
interacts with a suite.

## Design Intent

Rack is intentionally narrow:

- it is a test orchestration and reporting layer
- it does not own the code under test
- it does not own fixture hydration
- it does not require a custom execution model beyond normal `pytest`

Its job is to provide a consistent suite structure and durable reporting around
ordinary pytest tests.

## Main Concepts

### Suite

A Rack suite is a directory containing:

- `rack.toml`
- one or more stratum directories

The suite root becomes `TESTS_DIR`.

### Stratum

A stratum is an ordered layer of tests, usually named like:

- `L0_foundation`
- `L1_algorithms`
- `L5_roundtrip`

Each stratum contains:

- `STRATUM.toml`
- `test_*.py`
- optionally `cases/`

### Subtest

A subtest is one `test_*.py` file plus its matching `[[subtests]]` entry in
`STRATUM.toml`.

Rack reports and gates at the subtest file level, not only at the individual
test-function level.

### RackOutput

`RackOutput` is a structured side channel for attaching richer reporting data
to test execution.

Typical data:

- metrics
- timings
- comparisons
- attachments
- SVG outputs
- tags

## Package Runtime Model

### Suite Discovery

Rack resolves the active suite directory in this order:

1. `RACK_TESTS_DIR`
2. `WN_RACK_TESTS_DIR`
3. nearest parent of the current working directory containing `rack.toml`
4. current working directory

This allows both installed-package usage and compatibility wrappers.

### Derived Paths

Once `TESTS_DIR` is known, Rack derives:

- `RACK_CONFIG = TESTS_DIR / "rack.toml"`
- `RESULTS_DIR = TESTS_DIR / "rack_results"`
- `RACK_OUTPUT_DIR = RESULTS_DIR / "output"`
- `SOURCE_HASHES_FILE = RESULTS_DIR / "source_hashes.json"`
- `SOURCE_DIR = TESTS_DIR.parent`

It also discovers `PROJECT_ROOT` by walking upward from `TESTS_DIR` until it
finds `pyproject.toml`. That root is used as the working directory for pytest
execution.

## Command Layer

The CLI entrypoint is `rack.cli.main()`.

Command families:

- discovery and selection: `list`, `run`
- result consumption: `status`, `report`, `refresh`
- metadata analysis: `inventory`
- scaffolding: `new stratum`, `new subtest`

## Execution Flow

### 1. Configuration Load

Rack loads:

- suite-level `rack.toml`
- each targeted stratum's `STRATUM.toml`

### 2. Target Resolution

The requested target is resolved to:

- one or more strata
- optionally one subtest file
- optionally one pytest test function

Concern filtering is applied before pytest target list construction.

### 3. Helper Tool Build

If `rack.toml` declares `[[dependencies.internal_tools]]` for a selected
stratum, Rack builds those helper tools before invoking pytest.

### 4. Pytest Execution

Rack shells out to:

```text
uv run python -m pytest ...
```

It also enables `pytest-json-report` so one raw JSON report is written per
stratum:

- `rack_results/strata/<stratum>_pytest.json`

### 5. Subtest Aggregation

Rack groups pytest results by test file and computes subtest status using
electoral-college semantics:

- any failure in a file makes the subtest fail
- `xpassed` is treated as a failure bucket
- all-skipped files are treated as passing

### 6. Summary And Artifact Write

Rack writes:

- per-subtest JSON
- per-stratum JSON
- an amalgamated `summary.json`

The summary is updated incrementally so unaffected strata can remain in place
between targeted runs.

### 7. Source Staleness Update

For passing subtests, Rack records file hashes for modules declared in
`code_under_test`.

Tracked data includes:

- current file hash
- git commit hash
- git commit time
- git commit message
- per-subtest hash-at-test and timestamp

### 8. HTML Report Render

Rack regenerates `rack_results/report.html` automatically after `rack run`.

## Data Model Boundaries

### What Rack Interprets

Rack actively interprets:

- `rack.toml` strata ordering
- concern metadata for filtering
- helper-tool build metadata
- subtest file declarations
- `code_under_test`
- `test_cases`
- `test_case_type`

### What Rack Mostly Preserves For Reporting

Rack mostly treats these as rich metadata for display:

- stratum and subtest descriptions
- objectives
- approach data
- `test_functions`
- `bug_reference`
- custom descriptive fields under manifest tables

This is deliberate. Suites can carry domain-specific explanation without Rack
needing to understand every field semantically.

## Reporting Model

The HTML report merges several data sources:

- execution results from `summary.json`
- per-subtest metadata from `STRATUM.toml`
- inventory data from `test_cases`
- staleness data from `source_hashes.json`
- optional `RackOutput` payloads

This gives one report that is useful both as:

- a current execution dashboard
- a future-reference document for why a test exists and what it covers

## Inventory Model

`rack inventory` and the report inventory section scan for:

- declared `test_cases`
- subtests grouped by `test_case_type`
- orphaned directories under `cases/`

This is useful for:

- spotting dead fixture directories
- identifying missing metadata
- understanding corpus usage across strata

## Coverage Mapping Model

Rack also builds a reverse map from `code_under_test` declarations to tests.

It groups by module and then by:

- classes
- methods
- functions

This is not instrumentation-based coverage. It is declared traceability coverage
based on suite manifests.

## Extension Pattern

Rack is easiest to adopt when a project keeps these responsibilities separate:

- the project owns the code under test
- the project owns fixture hydration and environment setup
- Rack owns test orchestration, manifests, and reports

That keeps Rack reusable across codebases instead of turning it into a
project-specific bootstrap layer.

## Current Behavior Gaps

These are important because they affect documentation and future cleanup:

- `--all` is parsed but not yet wired to override enabled filtering
- `rack run` currently accepts one positional target token
- `rack status` can print a multi-token suggested run command
- `validate_code_under_test()` validates `module` directly, not the full
  `modules` list

## Package Structure

Current package layout:

```text
src/rack/
  __init__.py
  __main__.py
  cli.py
docs/
  architecture.md
  commands.md
  configuration.md
  index.md
  python-api.md
README.md
LICENSE
```
