# 🚀 aereo-search-aws-goes

The `aereo-search-aws-goes` plugin is a high-performance search component for the `aereo` ecosystem. It enables efficient discovery of GOES-R series satellite data (GOES-16 through GOES-19) stored in public AWS S3 buckets.

Powered by the [Polylith architecture](https://davidvujic.github.io/python-polylith-docs/setup/) and `uv`, this plugin provides a seamless way to query NOAA's GOES data archives without needing to manage complex S3 path logic.

---

## ✨ Features

*   **Multi-Satellite Support**: Discover data from GOES-16, GOES-17, GOES-18, and GOES-19.
*   **Product Coverage**: Supports a wide range of ABI Level 1b and Level 2 products, including:
    *   `ABI-L1b-RadF`: Full Disk
    *   `ABI-L1b-RadC`: CONUS
    *   `ABI-L1b-RadM`: Mesoscale
*   **Granular Filtering**: Filter results by exact time ranges and specific ABI channels (Bands 1-16).
*   **Comprehensive Metadata**: Returns `GeoPandas` dataframes containing:
    *   `href` (S3 URL) and `https_url` for immediate data access.
    *   Granule IDs, timestamps, and file sizes.
    *   Satellite, domain, and channel metadata.

---

## 📖 Usage Example

```python
from datetime import datetime, timezone
from shapely.geometry import box
from aereo.client import AereoClient
from aereo.search_aws_goes import SearchAwsGoes

# Define an AOI over the continental US
aoi = box(-105, 25, -85, 45)

# Configure the AWS GOES search provider
search_provider = SearchAwsGoes(
    collections={"ABI-L1b-RadC": ["C01"]},
    intersects=aoi,
    start_datetime=datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc),
    end_datetime=datetime(2025, 6, 1, 13, 0, tzinfo=timezone.utc),
    satellites=["GOES-16"],
)

# Search for GOES data
client = AereoClient()
results = client.search(search_provider=search_provider)

print(f"Found {len(results)} granules")
print(results[["collection", "start_time", "href"]].head())
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
