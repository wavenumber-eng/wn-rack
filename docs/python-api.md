# Python API

Rack exposes a small Python API for suites that want to attach richer data to
test execution.

## Public Surface

```python
from rack import (
    RackOutput,
    clear_current_output,
    get_current_output,
    set_current_output,
)
```

## `RackOutput`

`RackOutput` is a structured container for test-specific report data.

It is designed for data that does not fit naturally into pass/fail alone:

- metrics
- timings
- expected-vs-actual comparisons
- text attachments
- SVG outputs
- tags
- explicit status annotations

## Common Usage Pattern

Most suites expose a local fixture that creates one `RackOutput` per test,
stores it in Rack's thread-local slot, saves it at teardown, and clears it
afterward.

Example:

```python
from pathlib import Path

import pytest

from rack import RackOutput, clear_current_output, set_current_output


@pytest.fixture
def rack_output(request):
    output = RackOutput(
        test_id=request.node.nodeid,
        test_file=Path(request.node.fspath).name,
        test_name=request.node.name,
    )
    set_current_output(output)
    try:
        yield output
    finally:
        output.save()
        clear_current_output()
```

Test example:

```python
def test_roundtrip(rack_output):
    rack_output.add_metric("record_count", 42)
    rack_output.add_timing("parse_ms", 18.7)
    rack_output.add_comparison("hash", "abc123", "abc123")
    assert True
```

## Methods

### `add_metric(name, value)`

Stores a JSON-serializable metric value.

### `add_timing(name, duration_ms)`

Stores a timing value in milliseconds.

### `add_comparison(name, expected, actual, passed=None)`

Stores an expected-vs-actual comparison. If `passed` is omitted, Rack compares
`expected == actual`.

### `add_attachment(name, content)`

Stores a text attachment. The HTML report shows the attachment name; the full
content is written to the corresponding JSON payload.

### `add_svg_output(name, file_path, label="")`

Registers an SVG file for inline display in the HTML report.

### `add_tag(tag)`

Adds a tag to the output payload.

### `set_status(status, message="")`

Attaches a custom status annotation. This does not replace pytest's own test
outcome, but it gives the report an additional structured signal.

### `save(output_dir=None)`

Writes the payload as JSON. If `output_dir` is omitted, Rack uses
`<tests_dir>/rack_results/output/`.

## Thread-Local Helpers

### `get_current_output()`

Returns the current test's `RackOutput`, creating one if necessary.

### `set_current_output(output)`

Sets the current test's `RackOutput`.

### `clear_current_output()`

Clears the current test's `RackOutput`.

## Report Integration

Rack merges `RackOutput` payloads into:

- per-test output JSON
- the HTML report's metrics section
- comparison summaries
- SVG galleries

Suites that do not need this can ignore the API entirely and use Rack only as a
manifest-driven test runner.
