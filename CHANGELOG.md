# Changelog

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
