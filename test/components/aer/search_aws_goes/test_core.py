import pytest
from datetime import datetime, timezone
from unittest.mock import patch
import geopandas as gpd
from aer.search import SearchQuery
from aer.search_aws_goes import search_aws_goes
from aer.temporal import TimeRange
from aer.spectral import Product
from aer.spatial import GridSpatialExtent


def get_channel(pid, cid):
    return next(c for c in Product.get(pid).channels if c.c_id == cid)


ABI_L1B_RADF_AWS = Product.get("ABI-L1b-RadF")
ABI_BAND_1 = get_channel("ABI-L1b-RadF", "1")
ABI_BAND_13 = get_channel("ABI-L1b-RadF", "13")

# Dummy spatial extent for testing
DUMMY_SPATIAL_EXTENT = GridSpatialExtent(grid_cells=frozenset())


@patch("s3fs.S3FileSystem")
def test_search_aws_goes_empty(mock_s3_cls):
    time_range = TimeRange(
        start=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        end=datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc),
    )
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []
    query = SearchQuery(
        products=[ABI_L1B_RADF_AWS],
        time_range=time_range,
        spatial_extent=DUMMY_SPATIAL_EXTENT,
        satellites=(),
        channels=(),
    )
    gdf = search_aws_goes(query)
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert gdf.empty
    assert "product_name" in gdf.columns
    assert "channels" in gdf.columns
    assert "input_spatial_extent" in gdf.columns
    assert "overlapping_spatial_extent" in gdf.columns
    assert "cell_overlap_mode" in gdf.columns


@patch("s3fs.S3FileSystem")
def test_search_aws_goes_results(mock_s3_cls):
    time_range = TimeRange(
        start=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        end=datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc),
    )
    # Filename format: sYYYYJJJHHMMSS + optional digits
    filename = (
        "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc"
    )
    path = f"noaa-goes16/ABI-L1b-RadF/2024/001/12/{filename}"

    # Prefix our code will scan
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: (
        [{"name": path, "size": 1024 * 1024}] if p == prefix else []
    )

    query = SearchQuery(
        products=[ABI_L1B_RADF_AWS],
        time_range=time_range,
        spatial_extent=DUMMY_SPATIAL_EXTENT,
        satellites=(),
        channels=(),
    )
    gdf = search_aws_goes(query)

    assert not gdf.empty
    assert len(gdf) == 1
    assert gdf.iloc[0]["granule_id"] == filename
    assert gdf.iloc[0]["s3_url"] == f"s3://{path}"
    assert gdf.iloc[0]["size_mb"] == 1.0
    # channels column should contain the matching ABI band 1
    assert gdf.iloc[0]["channels"] == (ABI_BAND_1,)
    assert gdf.iloc[0]["input_spatial_extent"] == DUMMY_SPATIAL_EXTENT
    assert gdf.iloc[0]["overlapping_spatial_extent"] == DUMMY_SPATIAL_EXTENT
    assert gdf.iloc[0]["cell_overlap_mode"] == "contains"


@patch("s3fs.S3FileSystem")
def test_search_aws_goes_filters_by_channel(mock_s3_cls):
    """When query.channels is set, only files matching those bands are returned."""
    time_range = TimeRange(
        start=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        end=datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc),
    )

    # Three files: band 1, band 2, band 13
    filenames = [
        "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc",
        "OR_ABI-L1b-RadF-M6C02_G16_s20240011200000_e20240011209590_c20240011210000.nc",
        "OR_ABI-L1b-RadF-M6C13_G16_s20240011200000_e20240011209590_c20240011210000.nc",
    ]
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"
    files = [{"name": f"{prefix}{fn}", "size": 1024 * 1024} for fn in filenames]

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: files if p == prefix else []

    # Only request band 13
    query = SearchQuery(
        products=[ABI_L1B_RADF_AWS],
        time_range=time_range,
        channels=(ABI_BAND_13,),
        spatial_extent=DUMMY_SPATIAL_EXTENT,
        satellites=(),
    )
    gdf = search_aws_goes(query)

    assert len(gdf) == 1
    assert "C13" in gdf.iloc[0]["granule_id"]
    assert gdf.iloc[0]["channels"] == (ABI_BAND_13,)
    assert gdf.iloc[0]["input_spatial_extent"] == DUMMY_SPATIAL_EXTENT


@pytest.mark.integration
@pytest.mark.slow
def test_search_aws_goes_real():
    # Use a real satellite and time that we know has data
    time_range = TimeRange(
        start=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        end=datetime(2024, 1, 1, 12, 1, tzinfo=timezone.utc),
    )
    query = SearchQuery(
        products=[ABI_L1B_RADF_AWS],
        time_range=time_range,
        spatial_extent=DUMMY_SPATIAL_EXTENT,
        satellites=(),
        channels=(),
    )
    gdf = search_aws_goes(query)

    assert not gdf.empty, "Expected to find GOES files on AWS for 2024-001 12:00"
    assert "s3_url" in gdf.columns
    assert gdf.iloc[0]["product_name"] == "ABI-L1b-RadF"
    assert "input_spatial_extent" in gdf.columns
