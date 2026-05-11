# Contributing to aer-search-aws-goes

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
git clone https://github.com/<org>/aer-search-aws-goes.git
cd aer-search-aws-goes
uv sync --all-extras
```

### Testing

```bash
uv run pytest
uv run ruff check .
```

This plugin requires `aer` core for some tests. If you are developing alongside `aer` core, install it as an editable dependency.

### Plugin Structure

- `components/aer/search_aws_goes/` — plugin implementation
- `projects/aer-search-aws-goes/` — publishable package metadata
- `test/components/aer/search_aws_goes/` — unit tests
