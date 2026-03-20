# Configuration Reference

Rack reads two TOML files:

- suite-level `rack.toml`
- per-stratum `STRATUM.toml`

The minimum required fields are small. The richer fields mainly improve
reporting, traceability, and inventory views.

## Suite Layout

Rack expects a suite root like this:

```text
<tests_dir>/
  rack.toml
  L0_foundation/
    STRATUM.toml
    test_L0_001_example.py
    cases/
  L1_parsing/
    STRATUM.toml
    test_L1_001_example.py
    cases/
```

The suite root is whichever directory Rack resolves as `TESTS_DIR`.

## `rack.toml`

`rack.toml` is the suite-level configuration file.

### `[rack]`

Example:

```toml
[rack]
name = "Parser Regression Suite"
version = "1.0"
description = "Regression and conformance tests for a parser package"
note = "Nightly lane with HTML reporting and source staleness checks"
```

Fields Rack uses directly:

- `name`
  Human-friendly suite name.
- `version`
  Suite version string.
- `description`
  Human-friendly suite description.
- `note`
  Optional note shown by `rack status`.

### `[strata]`

Example:

```toml
[strata]
order = [
    "L0_foundation",
    "L1_parsing",
    "L2_roundtrip",
]
default_enabled = [
    "L0_foundation",
    "L1_parsing",
]
```

Fields:

- `order`
  Ordered list of stratum directory names. Rack uses this as the source of
  truth for ordering and for short-name prefix resolution such as `L0`.
- `default_enabled`
  Intended list of enabled strata. Current run selection still reads `enabled`
  from each `STRATUM.toml`. Rack's scaffold commands update this list for
  bookkeeping.

### `[concerns.*]`

Example:

```toml
[concerns.svg]
description = "SVG rendering"

[concerns.svg.text]
description = "SVG text rendering"
```

Concerns are hierarchical tags used for filtering and reporting. Rack treats
`svg` as matching `svg.text`.

Used by:

- `rack list --concern ...`
- `rack run --concern ...`
- HTML report displays

### `[dependencies]`

Example:

```toml
[dependencies]
python_packages = ["pytest", "pytest-json-report", "tomli-w"]
```

Current behavior:

- `python_packages` is informational today
- Rack does not install these automatically

### `[[dependencies.internal_tools]]`

Historical name aside, this table is simply for local helper tools that a suite
needs to build before running selected strata.

Example:

```toml
[[dependencies.internal_tools]]
name = "interop_probe"
path = "InteropProbe/"
purpose = "Native reference executable"
required_for = ["L4_comprehensive_interop"]
build_cmd = "powershell -ExecutionPolicy Bypass -File build.ps1"
output_exe = "bin/Publish/InteropProbe.exe"
```

Used by `rack run` before pytest execution for a target stratum.

Fields:

- `name`
  Display name shown in build logs.
- `path`
  Path relative to `TESTS_DIR`.
- `purpose`
  Informational only.
- `required_for`
  List of strata that require this tool.
- `build_cmd`
  Shell command to run inside `path`.
- `output_exe`
  Relative path under `path`. If it exists, Rack skips rebuilding unless forced
  internally.

## `STRATUM.toml`

Each stratum directory contains a `STRATUM.toml`.

Example outline:

```toml
name = "Foundation"
order = 0
description = "Low-level tests"
enabled = true
concerns = ["ole"]
objectives = ["Round-trip works"]

[[subtests]]
file = "test_L0_001_ole_roundtrip.py"
name = "OLE Round-Trip"
description = "Byte-for-byte verification"
test_cases = "../../../test_cases/altium/common/"
test_case_type = "reference"

[subtests.code_under_test]
module = "altium.altium_ole"
classes = ["AltiumOleFile"]
methods = ["open", "write"]
functions = ["file_hash"]

[subtests.objectives]
primary = "Verify byte-identical round-trips"
secondary = ["Mini stream handling", "Large file support"]

[subtests.approach]
summary = "Read, write, compare"
iterations = "Curated shared corpus"
parametrization = "pytest.mark.parametrize(...)"

[subtests.test_functions]
test_roundtrip = "Read -> Write -> Compare hash"
```

### Stratum-Level Fields

- `name`
  Human-friendly stratum name.
- `order`
  Numeric order, usually matching the `L0` / `L1` prefix.
- `description`
  Shown in list output and HTML reports.
- `enabled`
  Current gating field for `rack run` when no stratum is specified.
- `concerns`
  Default concern tags for the stratum.
- `objectives`
  List of stratum-level goals shown in reports.

### `[[subtests]]`

Each `[[subtests]]` entry describes one `test_*.py` file.

#### Core Fields

- `file`
  File name of the subtest, relative to the stratum directory.
- `name`
  Human-friendly subtest name.
- `description`
  Human-friendly summary.
- `concerns`
  Optional subtest-specific concerns. If missing, Rack falls back to stratum
  concerns for filtering.
- `test_cases`
  Path to the data used by the subtest. This may be local or shared.
- `test_case_type`
  Common values are `reference`, `synthetic`, and `algorithmic`.
- `bug_reference`
  Optional metadata displayed in the report.

#### `[subtests.code_under_test]`

This section drives traceability, validation, coverage mapping, and staleness.

Fields Rack uses directly:

- `module`
  Primary module path, such as `altium.altium_ole`.
- `modules`
  Additional modules used during source-hash updates in `cmd_run`.
- `classes`
  Class names used in coverage mapping and reporting.
- `methods`
  Method names used in coverage mapping and reporting.
- `functions`
  Function names used in coverage mapping and reporting.

Fields preserved for reporting but not interpreted deeply by Rack:

- `reference_implementation`
- `native_target`
- additional descriptive keys you choose to include

#### `[subtests.objectives]`

Common fields:

- `primary`
  Single primary goal string.
- `secondary`
  List of secondary goals.

These are shown in the HTML report but do not affect execution.

#### `[subtests.approach]`

Descriptive metadata for the report.

Common fields:

- `summary`
- `iterations`
- `parametrization`
- `comparison_method`
- `file_types`

Rack mainly treats this section as display metadata.

#### `[subtests.test_functions]`

Maps pytest function names to human-readable descriptions.

Example:

```toml
[subtests.test_functions]
test_roundtrip = "Read -> Write -> Compare hash"
test_error_path = "Verify missing stream raises ValueError"
```

This improves HTML report readability substantially.

## Path Semantics

### `test_cases`

Rack resolves `test_cases` relative to the stratum directory first:

```text
cases_full_path = <tests_dir>/<stratum>/<test_cases>
```

Relative upward paths are allowed and are commonly used when a suite shares a
larger external corpus.

### `code_under_test.module`

Rack resolves modules against the inferred source root:

- `<SOURCE_DIR>/<module_path>.py`
- `<SOURCE_DIR>/src/<module_path>.py`
- `<SOURCE_DIR>/src/py/<module_path>.py`

`SOURCE_DIR` is currently `TESTS_DIR.parent`.

## What Rack Generates

Scaffold commands generate a subset of this schema:

- `rack new stratum` creates:
  - stratum metadata
  - placeholder objectives
  - one placeholder `[[subtests]]`
- `rack new subtest` adds:
  - `file`, `name`, `description`
  - `code_under_test`
  - `objectives`
  - `approach`
  - `test_functions`

The generated templates are only a starting point. They still need real
metadata before the suite becomes useful.

## Current Validation Limits

Worth knowing today:

- `validate_code_under_test()` checks `code_under_test.module`
- it does not validate the full `modules` list yet
- `test_case_type` is used mostly for inventory and reporting, not gating
- extra fields are generally tolerated and preserved in manifest-loaded report
  data
