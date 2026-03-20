# Command Reference

This document describes the commands currently implemented by Rack.

## `rack list`

List configured strata or the subtests inside one stratum.

Examples:

```bash
rack list
rack list L0
rack list L0_foundation
rack list --concern svg
rack list L5 --concern pcb.svg
```

Behavior:

- without a positional argument, lists all strata from `rack.toml`
- with a positional argument, lists the subtests in one stratum
- `--concern` filters subtests by concern tag

## `rack run`

Run enabled strata, one stratum, one subtest, or one test function.

Examples:

```bash
rack run
rack run L0
rack run L0_foundation
rack run L5_001
rack run L5_001::test_name
rack run --concern svg
rack run L5 --test roundtrip
```

### Selection Rules

- no positional target:
  Rack runs strata whose `STRATUM.toml` has `enabled = true` or no explicit
  `enabled` field
- short stratum like `L0`:
  Rack resolves to the first configured stratum that starts with `L0_`
- subtest token like `L5_001`:
  Rack finds the subtest file whose stem starts with that prefix
- direct test token like `L5_001::test_name`:
  Rack runs one pytest function inside the resolved subtest file

### Execution Model

For each selected stratum, Rack:

1. validates `code_under_test.module` paths
2. builds any configured helper tools declared in
   `[[dependencies.internal_tools]]`
3. runs `uv run python -m pytest ... --json-report`
4. groups results by test file
5. computes subtest pass/fail
6. writes JSON artifacts
7. updates source-hash tracking for passing subtests
8. regenerates the HTML report

### Pass/Fail Semantics

Rack uses subtest-level pass/fail:

- a subtest passes only if it has no failures
- all-skipped subtests are treated as passing
- `xpassed` is counted as a failure bucket in subtest accounting

### Current Limitations

- `--all` is parsed but does not currently override enabled filtering
- the parser currently accepts one positional target token

## `rack status`

Show the current aggregated health of the suite.

Example:

```bash
rack status
```

Status includes:

- last updated timestamp
- optional suite note from `[rack].note`
- per-stratum pass/fail summary
- stale subtests based on source-hash comparison
- never-run strata
- a suggested `rack run ...` command to return to a clean state

## `rack report`

Regenerate the HTML report from existing JSON artifacts.

Example:

```bash
rack report
```

This does not rerun tests. It re-renders `rack_results/report.html`.

## `rack refresh`

Rebuild stratum JSON summaries from existing raw pytest JSON reports.

Example:

```bash
rack refresh
```

Use this when:

- raw `*_pytest.json` exists
- a `strata/<stratum>.json` summary needs rebuilding
- you want to regenerate durations and grouped test results without rerunning
  pytest

## `rack inventory`

Analyze declared test case usage and orphaned case directories.

Examples:

```bash
rack inventory
rack inventory --orphans
```

Reports:

- declared `test_cases` directories
- which subtests use them
- distribution by `test_case_type`
- orphaned directories under `cases/`
- missing `test_cases` / `test_case_type` metadata

## `rack new stratum`

Create a new stratum scaffold.

Example:

```bash
rack new stratum L2_roundtrip
```

Creates:

- `<tests_dir>/L2_roundtrip/`
- `<tests_dir>/L2_roundtrip/STRATUM.toml`
- `<tests_dir>/L2_roundtrip/cases/`
- `<tests_dir>/L2_roundtrip/test_L2_roundtrip_001_placeholder.py`

Also updates `rack.toml`:

- appends the new stratum to `[strata].order`
- appends it to `[strata].default_enabled`

### Naming Rules

The stratum name must match:

```text
L{n}_{name}
```

Examples:

- `L2_roundtrip`
- `L6_native_parity`

## `rack new subtest`

Create a new subtest file and append a matching manifest entry.

Examples:

```bash
rack new subtest L2 003 parser_smoke
rack new subtest L2_roundtrip 004 writer_regression
```

Creates:

- `test_<stratum>_<seq>_<name>.py`

And appends a matching `[[subtests]]` block to `STRATUM.toml`.

### Resolution Rules

- a short stratum like `L2` resolves to the first matching configured stratum
- if the stratum is not in `rack.toml`, Rack also checks for a matching
  directory

### Naming Rules

- `seq` is usually a three-digit string such as `003`
- `name` is normalized to snake_case for the file name

## Exit Codes

Current behavior:

- `rack run` returns `0` if all targeted strata pass, else `1`
- `rack list` returns `1` for unknown stratum
- `rack status` returns `0` even if failures or stale tests exist
- scaffold commands return `1` on validation or filesystem errors
