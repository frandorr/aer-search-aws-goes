# 🚀 aereo-search-aws-goes

The `aereo-search-aws-goes` plugin is a high-performance search component for the `aereo` ecosystem. It enables efficient discovery of GOES-R series satellite data (GOES-16 through GOES-19) stored in public AWS S3 buckets.

Powered by the [Polylith architecture](https://davidvujic.github.io/python-polylith-docs/setup/) and `uv`, this plugin provides a seamless way to query NOAA's GOES data archives without needing to manage complex S3 path logic.

---

## Installation

Add the plugin to your AEREO project with `uv`:

```bash
uv add aereo-search-aws-goes
```

Or with `pip`:

```bash
pip install aereo-search-aws-goes
```

Once installed, `aereo` automatically discovers the `search_aws_goes` plugin through Python entry points.

## ✨ Features

*   **Multi-Satellite Support**: Discover data from GOES-16, GOES-17, GOES-18, and GOES-19.
*   **Product Coverage**: Supports a wide range of ABI Level 1b and Level 2 products, including:
    *   `ABI-L1b-RadF`: Full Disk
    *   `ABI-L1b-RadC`: CONUS
    *   `ABI-L1b-RadM`: Mesoscale
*   **Granular Filtering**: Filter results by exact time ranges and specific ABI channels (Bands 1-16).
*   **Flood Products**: Discover NOAA ABI flood composites (`ABI-Flood-Day-TIF` / `ABI-Flood-Hourly-TIF`) via the `search_aws_goes_flood` plugin, with filtering by flood AOI tiles (e.g. `AOI004`).
*   **Comprehensive Metadata**: Returns `GeoPandas` dataframes containing:
    *   `href` (S3 URL) and `https_url` for immediate data access.
    *   Granule IDs, timestamps, and file sizes.
    *   Satellite, domain, and channel metadata.

---

## 📖 Usage Example

```python
from datetime import datetime, timezone
from shapely.geometry import box
from aereo.search_aws_goes import search_aws_goes

# Define an AOI over the continental US
aoi = box(-105, 25, -85, 45)

# Search for GOES data
results = search_aws_goes(
    collections={"ABI-L1b-RadC": ["C01"]},
    intersects=aoi,
    start_datetime=datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc),
    end_datetime=datetime(2025, 6, 1, 13, 0, tzinfo=timezone.utc),
    satellites=["GOES-16"],
)

print(f"Found {len(results)} granules")
print(results[["collection", "start_time", "href"]].head())
```

### Flood composites

```python
from datetime import datetime, timezone
from shapely.geometry import box
from aereo.search_aws_goes import search_aws_goes_flood

results = search_aws_goes_flood(
    collections={"ABI-Flood-Day-TIF": ["AOI004"]},
    intersects=box(-64, -34, -61, -31),
    start_datetime=datetime(2025, 11, 3, tzinfo=timezone.utc),
    end_datetime=datetime(2025, 11, 4, tzinfo=timezone.utc),
    satellites=["GOES-19"],
)
```

Or with Hydra:

```yaml
_target_: aereo.search_aws_goes.core:search_aws_goes
_partial_: true
collections:
  ABI-L1b-RadC: ["C01"]
intersects: config/aoi/us.geojson
start_datetime: "2025-06-01T12:00:00Z"
end_datetime: "2025-06-01T13:00:00Z"
satellites:
  - GOES-16
```

---

## 🏗️ Architecture

This repository follows the **Polylith** workspace structure:

*   **Components**: Core logic is located in `components/aereo/search_aws_goes/`.
*   **Projects**: The deployable PyPI package is defined in `projects/aereo-search-aws-goes/`.
*   **Tests**: Comprehensive unit and integration tests (mocked and live AWS) in `test/`.

---

## 🛠️ Development Workflow

If you are contributing to this plugin, follow these steps:

### 1. Setup
Initialize the environment and dependencies:
```bash
./setup.sh
```

### 2. Workspace Status
Check the status of components and projects:
```bash
uv run poly info
```

### 3. Running Tests
The test suite includes both mocked S3 tests and live integration tests:
```bash
# Run all tests
uv run pytest

# Run only fast, mocked tests
uv run pytest -m "not integration"
```

---

## 🚀 Releasing

This plugin uses [Conventional Commits](https://www.conventionalcommits.org/) and `python-semantic-release` for automated versioning.

1.  Commit changes with prefixes like `feat:`, `fix:`, or `chore:`.
2.  Run the release script:
    ```bash
    python3 .agents/scripts/release.py aereo-search-aws-goes
    ```

The CI/CD pipeline in `.github/workflows/release.yml` will automatically build and publish the package to PyPI upon new tag creation.

---

## 📜 License

This project is licensed under the [Apache License 2.0](LICENSE).
