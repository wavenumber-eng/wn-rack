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
uv sync --all-extras
uv run rack --version
uv run rack --help
uv run rack run --all
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
- `rack audit`
- `rack new stratum`
- `rack new subtest`
- `rack version`

Examples:

```bash
rack --version
rack version
rack run
rack run L0
rack run L5_001
rack run L5_001::test_name
rack list --concern svg.text
rack inventory --orphans
rack audit --format json
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

## Audit

`rack audit` checks Rack manifest drift without running tests. It exits nonzero
when the suite manifest and filesystem disagree.

It validates:

- `rack.toml` strata entries against stratum directories
- `STRATUM.toml` presence per stratum
- discovered `test_*.py` files against `[[subtests]].file`
- declared subtest files against the filesystem
- duplicate subtest ids and files
- conventional or configured signoff strata

Use `rack audit --strict` to promote missing inventory metadata such as
`test_cases` and `test_case_type` to failures.

`rack audit --format json` emits the versioned `rack.audit_report` `a0`
contract. The schema is committed at
`docs/contracts/rack_audit_report.a0.schema.json`.

## Python API

Rack exports:

- `RackOutput`
- `audit_suite`
- `get_current_output()`
- `set_current_output()`
- `clear_current_output()`

This is the structured-output side channel used by suites that want richer
report data such as metrics, timings, comparisons, attachments, or SVG output.

See [Python API](./docs/python-api.md).

## Documentation

- [Docs Index](./docs/index.md)
- [Setup](./docs/setup.html)
- [Configuration Reference](./docs/configuration.md)
- [Command Reference](./docs/commands.md)
- [CLI Design](./docs/design/cli.html)
- [Architecture](./docs/architecture.md)
- [Architecture Contract](./docs/architecture.html)
- [Public API Design](./docs/design/public-api.html)
- [Python API](./docs/python-api.md)

## Development Standards

Rack uses the Wavenumber Python baseline in legacy-adoption mode:

- committed `uv.lock`
- Rack self-hosted tests
- Ruff and Pyright gates
- HTML design docs and JSON contracts
- date-based release version `2026.7.16`
- GitHub Release published workflow with PyPI trusted publishing
- documented legacy exceptions for the current monolithic CLI module

Rack carries a `dev-std.toml` marker and a `docs.cli` command manifest so the
public CLI surface can be audited. Until the matching `wn-dev-std` release is
published, validate against a local dev-std checkout with Rack's `src` tree on
`PYTHONPATH`:

```powershell
$env:PYTHONPATH = "$PWD\src"
uv run --project <dev-std-checkout> dev-std audit . --scope docs.cli
```

After the dev-std release is published, use the normal project environment
instead of the local source checkout.

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
