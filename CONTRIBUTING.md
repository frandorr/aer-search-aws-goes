# Contributing to aereo-search-aws-goes

Thank you for your interest in contributing!

## General Guidelines

Please refer to the [AER core CONTRIBUTING.md](https://github.com/<org>/aer/blob/main/CONTRIBUTING.md) for:
- Reporting issues
- Development setup with uv and Polylith
- Pull request process
- Conventional Commits
- Code style (Ruff, basedpyright)

## Plugin-Specific Development

### Setup

```bash
git clone https://github.com/<org>/aereo-search-aws-goes.git
cd aereo-search-aws-goes
uv sync --all-extras
```

### Testing

```bash
uv run pytest
uv run ruff check .
```

This plugin requires `aer` core for some tests. If you are developing alongside `aer` core, install it as an editable dependency.

### Plugin Structure

- `components/aereo/search_aws_goes/` — plugin implementation
- `projects/aereo-search-aws-goes/` — publishable package metadata
- `test/components/aereo/search_aws_goes/` — unit tests
