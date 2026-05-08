import pytest
from datetime import datetime, timezone
from unittest.mock import patch
import geopandas as gpd

from aer.interfaces import AerProfile
from aer.search_aws_goes import GOES_EAST_C_POLY, GOES_WEST_F_POLY
from aer.search_aws_goes.core import AwsGoesSearchPlugin
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

    profile = AerProfile(name="goes", resolution=1000.0, collections=["ABI-L1b-RadF"])
    gdf = plugin.search(
        profiles=[profile],
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

    profile = AerProfile(name="goes", resolution=1000.0, collections=["ABI-L1b-RadF"])
    gdf = plugin.search(
        profiles=[profile],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params=None,
    )

    assert not gdf.empty
    assert len(gdf) == 1
    assert gdf.iloc[0]["collection"] == "ABI-L1b-RadF"


@patch("s3fs.S3FileSystem")
def test_search_reads_collections_from_profiles(mock_s3_cls):
    plugin = AwsGoesSearchPlugin()
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    filename = "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc"
    path = f"noaa-goes16/ABI-L1b-RadF/2024/001/12/{filename}"
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: [{"name": path, "size": 1024 * 1024}] if p == prefix else []

    profile = AerProfile(
        name="goes",
        resolution=1000.0,
        collections=["ABI-L1b-RadF"],
        satellite="GOES-16",
    )
    gdf = plugin.search(
        profiles=[profile],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params=None,
    )

    assert not gdf.empty
    assert all(r["collection"] == "ABI-L1b-RadF" for _, r in gdf.iterrows())


@patch("s3fs.S3FileSystem")
def test_search_filters_by_profile_channels(mock_s3_cls):
    plugin = AwsGoesSearchPlugin()
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)

    filenames = [
        "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc",
        "OR_ABI-L1b-RadF-M6C02_G16_s20240011200000_e20240011209590_c20240011210000.nc",
    ]
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"
    files = [{"name": f"{prefix}{fn}", "size": 1024 * 1024} for fn in filenames]

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: files if p == prefix else []

    profile = AerProfile(
        name="goes",
        resolution=1000.0,
        collections=["ABI-L1b-RadF"],
        channels=["C01"],
    )
    gdf = plugin.search(
        profiles=[profile],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params=None,
    )

    assert len(gdf) == 1
    assert gdf.iloc[0]["channel_id"] == "1"
    assert "C01" in gdf.iloc[0]["href"]
    assert "id" in gdf.columns
    assert "start_time" in gdf.columns
    assert "end_time" in gdf.columns
    assert "geometry" in gdf.columns


@patch("s3fs.S3FileSystem")
def test_search_aws_goes_filters_by_channel(mock_s3_cls):
    """When channels are set via profile, only files matching those bands are returned."""
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
    profile = AerProfile(
        name="goes",
        resolution=1000.0,
        collections=["ABI-L1b-RadF"],
        channels=["C13"],
    )
    gdf = plugin.search(
        profiles=[profile],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params=None,
    )

    assert len(gdf) == 1
    assert "C13" in gdf.iloc[0]["href"]
    assert gdf.iloc[0]["channel_id"] == "13"


@patch("s3fs.S3FileSystem")
def test_search_params_flow_through_to_s3fs(mock_s3_cls):
    """search_params kwarg should be forwarded to s3fs.S3FileSystem."""
    plugin = AwsGoesSearchPlugin()
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []

    profile = AerProfile(
        name="goes",
        resolution=1000.0,
        collections=["ABI-L1b-RadF"],
        search_params={"requester_pays": True},
    )
    # Simulate what AerClient.search() does: merge profile.search_params
    # over batch search_params and pass the merged dict as search_params.
    gdf = plugin.search(
        profiles=[profile],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params={"requester_pays": True},
    )

    assert isinstance(gdf, gpd.GeoDataFrame)
    kwargs = mock_s3_cls.call_args.kwargs
    assert kwargs["anon"] is True
    assert kwargs["requester_pays"] is True


@patch("s3fs.S3FileSystem")
def test_search_params_can_override_anon(mock_s3_cls):
    """search_params should be able to override the default anon=True."""
    plugin = AwsGoesSearchPlugin()
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []

    profile = AerProfile(name="goes", resolution=1000.0, collections=["ABI-L1b-RadF"])
    gdf = plugin.search(
        profiles=[profile],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params={"anon": False, "key": "abc", "secret": "xyz"},
    )

    assert isinstance(gdf, gpd.GeoDataFrame)
    kwargs = mock_s3_cls.call_args.kwargs
    assert kwargs["anon"] is False
    assert kwargs["key"] == "abc"
    assert kwargs["secret"] == "xyz"


@pytest.mark.integration
@pytest.mark.slow
def test_search_aws_goes_real():
    plugin = AwsGoesSearchPlugin()
    # Use a real satellite and time that we know has data
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 1, tzinfo=timezone.utc)

    profile = AerProfile(name="goes", resolution=1000.0, collections=["ABI-L1b-RadF"])
    gdf = plugin.search(
        profiles=[profile],
        intersects=None,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        search_params=None,
    )

    assert not gdf.empty, "Expected to find GOES files on AWS for 2024-001 12:00"
    assert "href" in gdf.columns
    assert gdf.iloc[0]["collection"] == "ABI-L1b-RadF"
