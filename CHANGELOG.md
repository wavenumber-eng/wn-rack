# Changelog

## Unreleased

- Remove the transitional `run(case, impl)` form: suite-registered adapters,
  comparators, loaders, catalogs, and services, the operation registry,
  `rack trace`, `rack.tracing`, `rack.outcomes`, typed differences, and
  performance budgets. A self-declared test is a plain pytest file with a
  `RACK` header; a file or header still using the old form fails with a
  message pointing to `docs/migrating-to-plain-tests.md`. The `operations`,
  `expect`, `cases_ref`, and `deferrals` audit requirements go, and the
  listed-code check reports `unresolved_code` (tally row `code`) in place of
  `untraced` (`trace`).
- A strict `xfail` on a plain row is recorded as a `deferred` row with its
  reason, and `rack parity` lists it as a deferred case. `rack parity` counts
  a plain test's cases from the vector files its header lists in `resources`.
- Implementation selection never skips a check's rows.
- A plain row the test skips itself (`pytest.skip` in its body) is recorded as
  skipped; before, Rack recorded it as a failure and `rack parity` counted it.
- Rack puts a suite's root on `sys.path` before it imports a self-declared
  module, so plain tests can import suite code.

- A header entry may name a native test (`"test": "<path>.rs"`); Rack runs it
  as one row, by status, and records pass or fail with cargo's output. Rust
  tests of one crate run in a single `cargo test --no-fail-fast` call per run,
  split per test binary. `rack audit` checks the file exists, reads
  the test's vector files, and follows the naming convention
  (`test_l0_004_<slug>.rs` with `fn l0_004_<slug>`; `test_L0_004_<slug>.cpp`).
- Self-declared tests require `purpose` (`checks` and `because`) in place of a
  module docstring, and every implementation uses one form:
  `{"status": ..., "reason": ..., "issue": ..., "code": [...]}`. Implemented
  implementations declare the code they exercise as `file`, `module`, and
  `function`; `rack audit` and collection verify each entry by reading only
  that file (Python, Rust, and C++ checkers; other file types fail).
  Suspended implementations that list code are verified the same way.
- `rack audit` reports every failing declaration requirement, tallies
  self-declared and without-`RACK` files per stratum plus test files outside
  any stratum, and fails files without `RACK` in strata that set
  `require_declared = true`. The JSON report moves to `rack.audit_report`
  `a1` with a `declarations` section.
- Rack's own legacy modules are excluded from `ruff format`.
- Code tracing moves to one operation registry: tests declare the operations
  they send (`RACK["operations"]`), and the registry records each
  implementation's dispatch file, handler, and library calls. Rack verifies
  the whole chain statically (audit code `untraced`, also at collection) and
  `rack trace` prints it with file and line numbers. Per-implementation `code`
  in test declarations is removed.
- Add self-declared tests (in progress on the `altium-monkey` branch): a test
  file carries a `RACK = {...}` literal and one `run(case, impl)` entry point.
  `rack.declarations` reads and validates declarations and JSON vector files
  statically; `rack.outcomes` provides exact typed comparison, exact-match
  deferrals, and canonical digests.
- Add the `rack.plugin` pytest plugin, auto-registered through the `pytest11`
  entry point. It expands each self-declared file into one item per (case,
  implementation), applies lanes and `--rack-impl` selection, and records the
  Rack outcome and typed differences in `user_properties`. Files without a
  top-level `RACK` assignment are untouched.
- `rack run --impl` selects implementations for self-declared tests. Subtest
  and stratum JSON carry each row's Rack outcome under `"rack"`.
- `rack list`, `run`, `report`, `refresh`, `inventory`, and `status` read
  self-declared files through the same declaration loader as the plugin;
  `rack audit` validates declarations, vector files, id collisions, and
  imports of test modules involving self-declared files.
- Add `rack parity` for per-stratum or per-concern accounting of
  implementation statuses, case outcomes, and debt, with the versioned
  `rack.parity_report` `a0` JSON contract. `rack report` adds a PARITY section
  with the implementation grid, per-test case outcomes, and the debt view.
- Add `rack run --jobs N` for strata that set `parallel = true`, using
  pytest-xdist (now a dependency) with `--dist loadfile`. Progress events are
  written per worker and merged by the progress tail.
- Accept a `[[subtests.code_under_test]]` array of tables so one subtest can
  declare several modules; validation, coverage mapping, source hashing, and
  the HTML report handle each block.
- Resolve `code_under_test` modules that are packages through their
  `__init__.py`.
- Fail manifest validation for a malformed `code_under_test` value instead of
  ignoring it.

## 2026.7.16

- Add native `rack audit` for Rack manifest/filesystem drift without running
  tests.
- Add the versioned `rack.audit_report` `a0` JSON output contract and schema.
- Export `audit_suite`, `AuditReport`, and `AuditFailure` for standards tools
  that consume Rack audit results directly.
- Dogfood latest dev-std CLI governance with an a0 command manifest and
  deterministic parser inventory provider.
- Harden audit release checks for invalid TOML with a stratum argument and
  duplicate `[strata].order` entries.

## 2026.6.24

- Add `rack.progress.ProgressReporter` and public `rack.ProgressReporter`
  export for long-running tests.
- Add `rack run --progress` / `--no-progress` and manifest-driven progress
  enablement through `progress = true` or long runtime profiles.
- Tail progress JSONL from the Rack runner and print live `RACK_PROGRESS`
  status lines to stderr.
- Add Rack-owned C++ progress helper scaffolding through
  `rack progress helper cpp --output ...`.
- Document the runtime progress JSONL contract and generated
  `rack_results/progress/` output location.

## 2026.6.4

- Move `wn-rack` from the old semantic-style version line to date-based version
  `2026.6.4`.
- Add `rack --version` and `rack version` with major runtime dependency
  versions.
- Add self-hosted Rack tests, release signoff, CI, trusted PyPI release
  workflow, and strict baseline documentation.
- Add HTML design docs, JSON contracts, release notes, and documented legacy
  exceptions for the current monolithic CLI module.

## 1.1.0

- Add PyPI release tooling.

## 1.0.0

- Initial package release.
