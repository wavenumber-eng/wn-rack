# Runtime Progress Reporting Design

Status: draft design

## Purpose

Long Rack tests should show that they are alive and what device-under-test
or file-under-test they are currently processing. This is separate from the
final pass/fail result and from rich post-run `RackOutput` artifacts.

The first adopter is the Altium Monkey C++ full-corpus SchDoc opaque-record
sweep, but the design must remain domain neutral so it can also support future
OrCAD/Cadence, stackup, SVG, IPC, and interop batch lanes.

## Scope

Rack owns:

- the progress event schema
- the output file location
- run-time environment variables passed to pytest and child tools
- optional console rendering/tailing behavior
- report integration for final progress summaries

Suites own:

- domain labels such as `SchDoc`, `PcbDoc`, `CaptureDesign`, or `StackupX`
- the current DUT identifier
- per-DUT metrics such as parse time, opaque count, rendered layers, or oracle
  validation status
- deciding which tests are long enough to emit progress

Rack should not know what a SchDoc or OrCAD design is.

## Runtime Contract

Rack creates a progress directory for each run:

```text
rack_results/progress/
```

Rack exposes these environment variables to pytest and any native child tools
launched by the suite:

```text
RACK_PROGRESS=1
RACK_PROGRESS_DIR=<tests_dir>/rack_results/progress
RACK_PROGRESS_RUN_ID=<stable run id>
RACK_PROGRESS_STDERR=1
```

`RACK_PROGRESS=1` enables progress emission. `RACK_PROGRESS_STDERR=1` asks
test code to also emit human-readable progress lines to stderr. The JSONL file
is the durable source of truth.

Rack may also set a test-level progress file when it can determine the active
subtest:

```text
RACK_PROGRESS_FILE=<tests_dir>/rack_results/progress/<run_id>.jsonl
```

If `RACK_PROGRESS_FILE` is absent, suite helpers derive it from
`RACK_PROGRESS_DIR` and `RACK_PROGRESS_RUN_ID`.

## Event Schema

Progress files are newline-delimited JSON. Each line is one event.

Required fields:

```json
{
  "schema": "rack.progress.v0",
  "timestamp": "2026-06-24T15:04:05.123456Z",
  "run_id": "20260624T150405Z-12345",
  "test_id": "test_L4_125_schdoc_cpp_no_opaque_test_tree",
  "event": "progress"
}
```

Recommended fields:

```json
{
  "phase": "parse",
  "done": 137,
  "total": 1047,
  "dut_kind": "SchDoc",
  "dut_id": "altium/common/example/input/foo.SchDoc",
  "elapsed_s": 52.3,
  "worker_count": 8,
  "message": "parsed SchDoc",
  "metrics": {
    "parse_s": 0.184,
    "object_count": 1435,
    "opaque_object_count": 0
  }
}
```

Allowed `event` values:

- `start`: test or batch started
- `progress`: one or more DUTs completed
- `warning`: non-fatal issue
- `failure`: a DUT or phase failed
- `finish`: test or batch finished

`done` and `total` are counts for the current test/batch. `dut_id` should be
stable and human-readable. Paths should be relative to the suite or corpus root
when possible.

## Console Format

When `RACK_PROGRESS_STDERR=1`, helpers should also write a short tab-delimited
line to stderr and flush immediately:

```text
RACK_PROGRESS	test_L4_125_schdoc_cpp_no_opaque_test_tree	137/1047	SchDoc	altium/.../foo.SchDoc	elapsed=52.3s
```

Console output is advisory. Pytest and CTest may capture it, and some runners
only display it on failure. The JSONL file remains authoritative.

## Throttling

Long tests should avoid noisy output:

- always emit `start`
- always emit `finish`
- always emit `failure`
- emit `progress` at most once every 5 seconds by default
- allow forced progress after fixed item counts for very fast batches

The helper should track the latest DUT internally so a stalled run still has a
recent progress file entry.

## Python Suite API

Proposed public API:

```python
from rack.progress import ProgressReporter

reporter = ProgressReporter(
    test_id="test_L4_125_schdoc_cpp_no_opaque_test_tree",
    total=1047,
    dut_kind="SchDoc",
)
reporter.start(worker_count=8)
reporter.progress(done=137, dut_id="altium/.../foo.SchDoc", metrics={"parse_s": 0.184})
reporter.finish(done=1047, failures=0)
```

This API is optional. Existing suites continue to work without it.

## Native Tool Contract

C++ and other native tools do not need to link against Rack. They can write
the JSONL contract directly using environment variables.

Recommended native helper responsibilities:

- read `RACK_PROGRESS`, `RACK_PROGRESS_FILE`, `RACK_PROGRESS_DIR`, and
  `RACK_PROGRESS_RUN_ID`
- append JSONL atomically enough for one-process or controlled multi-worker
  tests
- write stderr lines only when `RACK_PROGRESS_STDERR=1`
- include a fallback no-op implementation when progress is disabled

For Altium Monkey this helper should live near the native test harness first,
for example:

```text
src/cpp/tests/common/altium_test_progress.h
```

If more native projects adopt the same pattern, the helper can later move into
`wn-dev-std`.

## Rack CLI Behavior

Initial Rack implementation should be conservative:

- `rack run --progress` enables progress output.
- `rack run --no-progress` disables it.
- For subtests marked with a long/full-corpus metadata flag, Rack may enable
  progress by default.
- Rack should print the progress JSONL path at the start of a run when progress
  is enabled.
- Live tailing is optional for the first implementation. Writing durable JSONL
  and enabling helpers is enough to unblock long tests.

Future CLI behavior can include:

- `rack progress` to show the latest progress file
- `rack run --progress=tail` to print live progress updates
- HTML report summaries showing elapsed time, final counts, and slowest DUTs

## Manifest Metadata

Long tests should be explicit in `STRATUM.toml`:

```toml
runtime_profile = "full_corpus"
progress = true
```

Rack may also infer progress from concern tags such as:

```toml
concerns = ["schdoc", "corpus", "full_corpus"]
```

Explicit `progress = false` should win over inferred behavior.

## First Adoption Plan

1. Implement `rack.progress.ProgressReporter` and environment setup in Rack.
2. Add Rack self-tests for JSONL emission and disabled no-op behavior.
3. Release Rack publicly to PyPI.
4. Update the Altium Monkey private suite to use the released Rack version.
5. Add an Altium C++ native progress helper.
6. Promote `test_L4_125_schdoc_cpp_no_opaque_test_tree.cpp` as a long/full
   corpus native lane using progress output.
7. Backfill C++ strata as they are completed or touched during the C++ test
   realignment plan.

Python Altium tests do not need an immediate backfill. New or edited long
Python tests should adopt `ProgressReporter` as a ratchet.

## Non-Goals

- Rack will not replace pytest progress output.
- Rack will not parse domain-specific DUT identifiers.
- Rack will not require every test to emit progress.
- Rack will not make progress events part of pass/fail semantics in the first
  implementation.
