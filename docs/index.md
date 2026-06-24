# Documentation

This package has four main reference documents:

- [Configuration Reference](./configuration.md)
  Explains `rack.toml`, `STRATUM.toml`, and the manifest fields Rack reads.
- [Command Reference](./commands.md)
  Documents the CLI commands and their current behavior.
- [Architecture](./architecture.md)
  Describes how Rack is structured internally and how it executes a suite.
- [Python API](./python-api.md)
  Covers `RackOutput` and the small runtime API exposed to test suites.
- [Setup](./setup.html)
  Captures the current uv, signoff, and release workflow.
- [HTML CLI Design](./design/cli.html)
  Provides machine-checkable public command design anchors.
- [HTML Public API Design](./design/public-api.html)
  Provides machine-checkable public interface design anchors.
- [Runtime Progress Reporting Design](./design/runtime-progress.md)
  Defines the draft JSONL and environment contract for long-running tests.

For a first read, start with:

1. the top-level [README](../README.md)
2. [Configuration Reference](./configuration.md)
3. [Command Reference](./commands.md)

Use the architecture and Python API docs as the deeper reference set.
