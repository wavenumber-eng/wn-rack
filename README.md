# wn-rack

`wn-rack` is a packageable test runner built on top of `pytest`.

It adds a suite model around ordinary `pytest` tests:

- ordered strata such as `L0_foundation` and `L1_parsing`
- TOML manifests for suite and subtest metadata
- durable JSON result artifacts
- HTML reporting
- declared source-to-test traceability through `code_under_test`
- lightweight scaffolding for new strata and subtests

The distribution name is `wn-rack`. The CLI command is `rack`.

## Install

```bash
pip install wn-rack
```

For local package development:

```bash
uv sync
uv run rack --help
```

## What Rack Does

Rack does not replace `pytest` authoring. Tests stay as normal `test_*.py`
files. Rack adds:

- `rack.toml` for suite-wide configuration
- `STRATUM.toml` for per-stratum manifests
- subtest tracking at the test-file level
- persisted results in `rack_results/`
- suite status and inventory views
- HTML reports that merge execution data and manifest metadata

## Quick Start

Minimal suite layout:

```text
my_suite/
  rack.toml
  L0_foundation/
    STRATUM.toml
    test_L0_001_smoke.py
    cases/
  L1_parsing/
    STRATUM.toml
    test_L1_001_parser.py
    cases/
```

Minimal `rack.toml`:

```toml
[rack]
name = "Example Suite"
version = "1.0"
description = "Example Rack suite"

[strata]
order = ["L0_foundation", "L1_parsing"]
default_enabled = ["L0_foundation", "L1_parsing"]
```

Minimal `L0_foundation/STRATUM.toml`:

```toml
name = "Foundation"
order = 0
description = "Low-level tests"
enabled = true
concerns = ["core"]
objectives = ["Verify the basic behavior works"]

[[subtests]]
file = "test_L0_001_smoke.py"
name = "Smoke"
description = "Basic smoke coverage"
test_case_type = "algorithmic"

[subtests.code_under_test]
module = "example_module"

[subtests.test_functions]
test_smoke = "Basic sanity check"
```

Minimal test file:

```python
def test_smoke():
    assert True
```

Run the suite:

```bash
rack run
rack status
rack report
```

## Suite Resolution

Rack resolves the active suite root in this order:

1. `RACK_TESTS_DIR`
2. `WN_RACK_TESTS_DIR`
3. nearest parent of the current working directory containing `rack.toml`
4. current working directory

This supports both package-first usage and legacy wrapper scripts.

## Main Commands

- `rack list`
- `rack run`
- `rack status`
- `rack report`
- `rack refresh`
- `rack inventory`
- `rack new stratum`
- `rack new subtest`

Examples:

```bash
rack run
rack run L0
rack run L5_001
rack run L5_001::test_name
rack list --concern svg.text
rack inventory --orphans
```

## Result Artifacts

Rack writes output under `<tests_dir>/rack_results/`:

- `summary.json`
- `source_hashes.json`
- `report.html`
- `subtests/*.json`
- `strata/<stratum>.json`
- `strata/<stratum>_pytest.json`
- `output/*.json`

## Python API

Rack exports:

- `RackOutput`
- `get_current_output()`
- `set_current_output()`
- `clear_current_output()`

This is the structured-output side channel used by suites that want richer
report data such as metrics, timings, comparisons, attachments, or SVG output.

See [Python API](./docs/python-api.md).

## Documentation

- [Docs Index](./docs/index.md)
- [Configuration Reference](./docs/configuration.md)
- [Command Reference](./docs/commands.md)
- [Architecture](./docs/architecture.md)
- [Python API](./docs/python-api.md)

## Current Behavior Notes

These are current implementation realities:

- `--all` is parsed but does not yet override enabled filtering
- `rack run` currently accepts one positional target token
- `rack status` can suggest multiple short names in one command string even
  though the parser takes one positional target
- `validate_code_under_test()` checks `code_under_test.module` directly, while
  `cmd_run()` also supports `modules` for source-hash updates

## License

MIT. See [LICENSE](./LICENSE).
