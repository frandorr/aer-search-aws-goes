# Examples — aer-search-aws-goes

This directory contains runnable examples demonstrating how to use the `aer-search-aws-goes` plugin.

## Prerequisites

1. **Install dependencies** from the workspace root:
   ```bash
   cd ..
   uv sync --all-extras
   ```

2. **No authentication required** — GOES data on AWS S3 is publicly accessible.

## Running an Example

```bash
uv run python examples/basic_search.py
```

## Files

| File | Description |
|------|-------------|
| `basic_search.py` | Search for GOES-16 ABI CONUS radiance data over the continental US. |
