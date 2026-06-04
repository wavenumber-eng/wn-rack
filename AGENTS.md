# Agent Guide

`wn-rack` is Wavenumber's strata-based pytest orchestration and reporting
tool. It is infrastructure used by other packages, so changes should be small,
tested, and backward-compatible unless a breaking change is explicitly planned.

## Setup

Use `uv` for local development:

```bash
uv sync --all-extras
```

Commit `uv.lock`. Do not hand-edit it.

## Test And Signoff

Run the package signoff before release-facing changes:

```bash
uv run rack run --all
uv run python -m build
uv run twine check dist/*
```

Fast local checks:

```bash
uv run rack run L0_public_cli
uv run rack run L1_self_hosting
```

## Architecture Boundaries

`src/rack/cli.py` is currently a legacy monolith that owns CLI setup,
configuration, execution, reporting, scaffolding, and the public `RackOutput`
API. New code should move toward smaller modules rather than increasing this
file.

Current planned split:

- command files
- configuration and suite discovery
- target selection
- pytest execution and aggregation
- HTML report rendering
- inventory and source-hash reporting
- scaffolding
- public output API

## Release Rules

- `main` should represent the latest released/tagged source.
- Public changes should merge through PRs with required CI.
- Release publication should trigger validation and trusted PyPI publishing.
- Version `2026.6.4` starts the date-versioned release line.
- `CHANGELOG.md` and `docs/releases/<YYYY-MM-DD>.md` must mention the current
  package version.

## Local Secrets

Do not commit `.env` files or PyPI tokens. PyPI publishing should use trusted
publishing, not checked-in or local token scripts.

## Legacy Exceptions

Strict rules are the target. Legacy exceptions must be documented in
`docs/contracts/exceptions.json` and should ratchet down over time.
