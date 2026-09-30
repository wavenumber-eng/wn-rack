# Migrating to plain tests

Rack's transitional `run(case, impl)` form was removed on 2026-09-30. A
self-declared test is now only an ordinary pytest file with a `RACK` header
and one `test_*` function (see [the design](./design/self-declared-tests.md)).
Rack decides which rows run from the header and records their outcomes; the
test does its own work.

A file still written the old way fails with `the run(case, impl) form was
removed`, and a header that still uses `cases`, `observation`, `expect`,
`operations`, or `deferred` fails with a message naming the removed form.

## Converting a file

Before:

```python
from suite_support.operations import ParseDuration

RACK = {
    "id": "L0_001",
    "title": "Duration parsing",
    "purpose": {"checks": "...", "because": "..."},
    "operations": ["ParseDuration"],
    "cases": {"file": "vectors/L0_001_parse_duration.json"},
    "observation": "DurationResult",
    "expect": {"source": "contract", "comparator": "exact"},
    "implementations": {
        "python": {"status": "implemented"},
        "shadow": {"status": "implemented"},
        "rust": {"status": "planned", "reason": "not ported yet", "issue": "#1"},
    },
    "deferred": {"shadow": {"fractional_hours": {"issue": "#2", "differences": [...]}}},
}


def run(case, impl):
    (result,) = impl.batch([ParseDuration(text=case.inputs["text"])])
    return result
```

After:

```python
import json
from pathlib import Path

import pytest
from suite_support import durations

RACK = {
    "id": "L0_001",
    "title": "Duration parsing",
    "purpose": {"checks": "...", "because": "..."},
    "resources": ["vectors/L0_001_parse_duration.json"],
    "implementations": {
        "python": {"status": "implemented"},
        "shadow": {"status": "implemented"},
        "rust": {"status": "planned", "reason": "not ported yet", "issue": "#1"},
    },
}

VECTORS = Path(__file__).parent / "vectors" / "L0_001_parse_duration.json"
CASES = json.loads(VECTORS.read_text(encoding="utf-8"))["cases"]
KNOWN_FAILURES = {("fractional_hours", "shadow"): "#2: shadow truncates fractional amounts"}


def unported(text):
    raise NotImplementedError("no port")


IMPLEMENTATIONS = {
    "python": durations.parse_duration,
    "shadow": durations.parse_duration_shadow,
    "rust": unported,
}


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_duration_parsing(case, implementation, request):
    known = KNOWN_FAILURES.get((case["id"], implementation))
    if known:
        request.applymarker(pytest.mark.xfail(strict=True, reason=known))
    assert IMPLEMENTATIONS[implementation](case["inputs"]["text"]) == case["expect"]["seconds"]
```

A file that runs only one implementation needs no `IMPLEMENTATIONS` table: its
test runs the first implementation listed without a native `test`. An
implementation in another language is a native test named in its header
entry (`"test": "crate/tests/test_l0_001_parse_duration.rs"`).

## What replaces each removed piece

| Removed | Replacement |
|---|---|
| `cases: {"file": ...}` | Read the vector file in the test, parametrize over a parameter named `case`, and list the file in `resources`. |
| `cases: {"catalog": ...}` and suite catalogs | Build the case list in the test module (for example with a glob) and parametrize over it. |
| `observation`, `expect`, comparators | Assert in the test body against the vector file's `expect`, a reference file, or a computed property. |
| Authority and property loaders | Read the reference file or compute the property in the test. |
| `expect.source = "budget"` | Measure in the test body and assert the budget there. |
| `operations`, the operation registry, adapters, `impl.batch` | Call the implementation directly; list the functions each implementation exercises in its `code` entries, which `rack audit` resolves. |
| `rack trace` | The header's `code` entries and the audit's `code` rule. |
| `deferred` | A strict `xfail` on the row, with the tracking issue in its reason; in a native test, a `rack-deferred: <case id>: <reason>` line for each known failure it tolerates. |
| Services | Module-level helpers or pytest fixtures. |
| Case lanes | The test decides which cases it runs. |
| `case.workdir` | pytest's `tmp_path`. |

## Suite configuration

Remove these from `rack.toml`: `adapter` entries under
`[implementations.<name>]`, `[operations]`, `[catalogs]`, `[loaders]`, and
`[services]`. `[implementations.<name>] parallel = false` still serializes
strata while that implementation is selected. Test modules can still import
suite code from the suite root: Rack puts the root on `sys.path` before it
imports a self-declared module.

## Changed reports

- `rack audit` no longer has the `operations`, `expect`, `cases_ref`, or
  `deferrals` requirements. The listed-code check reports `unresolved_code`
  (tally row `code`) in place of `untraced` (`trace`).
- Implementation selection (`--rack-impl`, `rack run --impl`) never skips a
  check's rows.
- A strict `xfail` row is recorded as `deferred`, with its reason, and
  `rack parity` lists it under deferred cases. `rack audit` rejects a
  non-strict `xfail` mark and `pytest.xfail()` (`known_failure`).
- A passing native test that prints `rack-deferred: <case id>: <reason>`
  lines is a `deferred` row with those lines as its detail.
- A row whose setup or teardown fails is an `error` row.
- `rack parity` counts a test's cases from the vector files its header lists
  in `resources`, and from its latest rows when it lists none.
