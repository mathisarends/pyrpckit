# AGENTS.md

Guidance for AI coding agents working in this repository.

## Project

`pyrpckit` is a Python library, packaged for distribution on PyPI.

## Environment

- Dependency management: [uv](https://docs.astral.sh/uv/)
- Supported Python versions: 3.12–3.14
- Install dependencies: `uv sync --all-groups`

## Workflow

- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- Tests: `uv run pytest`
- Pre-commit hooks are configured in `.pre-commit-config.yaml`; install with `uv run pre-commit install`.

## Conventions

- Library code lives in `pyrpckit/`.
- Tests live in `tests/` and mirror the package structure.
- Keep `pyproject.toml` as the single source of truth for metadata and tool config.
