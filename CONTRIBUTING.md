# Contributing

## Development

Use `uv` and Rack:

```bash
uv sync --all-extras
uv run rack run --all
```

Run package validation before release-facing changes:

```bash
uv run python -m build
uv run twine check dist/*
```

## Pull Requests

Pull requests should keep code and docs aligned:

- update tests with behavior changes
- update `docs/design/*.html` for public command or API changes
- update `docs/contracts/*` when command/interface contracts change
- update `CHANGELOG.md` and release notes for release-facing changes

## Legacy Refactors

`src/rack/cli.py` is intentionally grandfathered while Rack adopts the new
standard. Do not expand the monolith for unrelated functionality; prefer
extracting stable modules after tests cover the behavior.
