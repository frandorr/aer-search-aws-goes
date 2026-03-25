import pytest
from datetime import datetime, timezone
from unittest.mock import patch
import geopandas as gpd
from aer.search import SearchQuery
from aer.search_aws_goes import search_aws_goes, serialize_search_results, deserialize_search_results
from aer.temporal import TimeRange
from aer.spectral import Product, Channel
from aer.spatial import GridSpatialExtent, GridCell
from shapely.geometry import Polygon


def get_channel(pid, cid):
    return next(c for c in Product.get(pid).channels if c.c_id == cid)


ABI_L1B_RADF_AWS = Product.get("ABI-L1b-RadF")
ABI_BAND_1 = get_channel("ABI-L1b-RadF", "1")
ABI_BAND_13 = get_channel("ABI-L1b-RadF", "13")

# Dummy spatial extent for testing
TEST_CELL = GridCell(
    row="100U",
    col="100R",
    dist=100,
    bounds=Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
    epsg="EPSG:4326",
)
DUMMY_SPATIAL_EXTENT = GridSpatialExtent(grid_cells=frozenset([TEST_CELL]))


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
    assert "product_id" in gdf.columns
    assert "channel" in gdf.columns
    assert "unique_id" in gdf.columns
    assert "name" in gdf.columns
    assert "overlap_mode" in gdf.columns


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
    assert gdf.iloc[0]["channel"] == ABI_BAND_1
    assert gdf.iloc[0]["overlap_mode"] == query.cell_overlap_mode
    assert gdf.iloc[0]["product_id"] == "ABI-L1b-RadF"


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
    assert gdf.iloc[0]["channel"] == ABI_BAND_13


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
    assert gdf.iloc[0]["product_id"] == "ABI-L1b-RadF"


def test_serialization_deserialization():
    """Verify that search results can be serialized for Parquet and restored."""
    time_range = TimeRange(
        start=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        end=datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc),
    )
    # Filename format: sYYYYJJJHHMMSS + optional digits
    filename = (
        "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc"
    )
    path = f"noaa-goes16/ABI-L1b-RadF/2024/001/12/{filename}"
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"

    with patch("s3fs.S3FileSystem") as mock_s3_cls:
        mock_fs = mock_s3_cls.return_value
        mock_fs.ls.side_effect = lambda p, detail=False: (
            [{"name": path, "size": 1024 * 1024}] if p == prefix else []
        )

        query = SearchQuery(
            products=[ABI_L1B_RADF_AWS],
            time_range=time_range,
            channels=(ABI_BAND_1,),
            spatial_extent=DUMMY_SPATIAL_EXTENT,
            satellites=(),
        )
        gdf = search_aws_goes(query)

    assert not gdf.empty
    assert isinstance(gdf.iloc[0]["channel"], Channel)

    # Serialize
    serialized_df = serialize_search_results(gdf)
    # Channel object should be converted to ID string
    assert isinstance(serialized_df.iloc[0]["channel"], str)
    assert serialized_df.iloc[0]["channel"] == "1"

    # Deserialize
    deserialized_gdf = deserialize_search_results(serialized_df)
    # Channel ID should be converted back to Channel object
    assert isinstance(deserialized_gdf.iloc[0]["channel"], Channel)
    assert deserialized_gdf.iloc[0]["channel"] == ABI_BAND_1
