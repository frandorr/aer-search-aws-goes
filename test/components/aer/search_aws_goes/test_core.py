import pytest
from datetime import datetime, timezone
from unittest.mock import patch
import geopandas as gpd

from aer.search_aws_goes.core import AwsGoesSearchPlugin, GOES_EAST_C_POLY, GOES_WEST_F_POLY
from shapely.geometry import MultiPolygon, Polygon


def test_goes_polygon_extraction():
    # CONUS footprint should not cross the antimeridian, returning a regular Polygon
    assert isinstance(GOES_EAST_C_POLY, Polygon)
    assert not GOES_EAST_C_POLY.is_empty

    # GOES-West Full Disk crosses the antimeridian and must be split into a MultiPolygon
    assert isinstance(GOES_WEST_F_POLY, MultiPolygon)
    assert not GOES_WEST_F_POLY.is_empty

    # Check that GOES-West Full Disk has outer bounds bounded strictly by standard coordinate boundaries
    bounds = GOES_WEST_F_POLY.bounds
    assert pytest.approx(bounds[0], abs=0.1) == -180.0
    assert pytest.approx(bounds[2], abs=0.1) == 180.0


@patch("s3fs.S3FileSystem")
def test_search_aws_goes_empty(mock_s3_cls):
    plugin = AwsGoesSearchPlugin()
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []

    gdf = plugin.search(
        collections=["ABI-L1b-RadF"],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params=None,
    )

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert gdf.empty
    assert "id" in gdf.columns
    assert "collection" in gdf.columns
    assert "href" in gdf.columns


@patch("s3fs.S3FileSystem")
def test_search_aws_goes_results(mock_s3_cls):
    plugin = AwsGoesSearchPlugin()
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    # Filename format: sYYYYJJJHHMMSS + optional digits
    filename = "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc"
    path = f"noaa-goes16/ABI-L1b-RadF/2024/001/12/{filename}"

    # Prefix our code will scan
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: [{"name": path, "size": 1024 * 1024}] if p == prefix else []

    gdf = plugin.search(
        collections=["ABI-L1b-RadF"],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params=None,
    )

    assert not gdf.empty
    assert len(gdf) == 1
    assert gdf.iloc[0]["collection"] == "ABI-L1b-RadF"
    assert gdf.iloc[0]["href"] == f"s3://{path}"
    assert "id" in gdf.columns
    assert "start_time" in gdf.columns
    assert "end_time" in gdf.columns
    assert "geometry" in gdf.columns


@patch("s3fs.S3FileSystem")
def test_search_aws_goes_filters_by_channel(mock_s3_cls):
    """When channels are set via search_params, only files matching those bands are returned."""
    plugin = AwsGoesSearchPlugin()
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)

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
    gdf = plugin.search(
        collections=["ABI-L1b-RadF"],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params={"channels": ["13"]},
    )

    assert len(gdf) == 1
    assert "C13" in gdf.iloc[0]["href"]
    assert gdf.iloc[0]["channel_id"] == "13"


@pytest.mark.integration
@pytest.mark.slow
def test_search_aws_goes_real():
    plugin = AwsGoesSearchPlugin()
    # Use a real satellite and time that we know has data
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 1, tzinfo=timezone.utc)

    gdf = plugin.search(
        collections=["ABI-L1b-RadF"],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params=None,
    )

    assert not gdf.empty, "Expected to find GOES files on AWS for 2024-001 12:00"
    assert "href" in gdf.columns
    assert gdf.iloc[0]["collection"] == "ABI-L1b-RadF"
