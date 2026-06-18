import pytest
from datetime import datetime, timezone
from unittest.mock import patch
import geopandas as gpd

from aereo.search_aws_goes import GOES_EAST_C_POLY, GOES_WEST_F_POLY
from aereo.search_aws_goes.core import SearchAwsGoes
from aereo.search_aws_goes.utils import _parse_domain
from shapely.geometry import MultiPolygon, Polygon, box


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


def test_parse_domain_abi():
    assert _parse_domain("ABI-L1b-RadC") == "C"
    assert _parse_domain("ABI-L1b-RadF") == "F"
    assert _parse_domain("ABI-L1b-RadM") == "M"


def test_parse_domain_glm():
    assert _parse_domain("GLM-L2-LCFA") == "F"


def test_parse_domain_unknown():
    with pytest.raises(ValueError, match="Unknown GOES domain"):
        _parse_domain("ABI-L1b-RadX")


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_aws_goes_empty(mock_s3_cls):
    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc),
    )
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []

    gdf = plugin()

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert gdf.empty
    assert "id" in gdf.columns
    assert "collection" in gdf.columns
    assert "href" in gdf.columns


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_aws_goes_results(mock_s3_cls):
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    # Filename format: sYYYYJJJHHMMSS + optional digits
    filename = "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc"
    path = f"noaa-goes16/ABI-L1b-RadF/2024/001/12/{filename}"

    # Prefix our code will scan
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: [{"name": path, "size": 1024 * 1024}] if p == prefix else []

    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
    )
    gdf = plugin()

    assert not gdf.empty
    assert len(gdf) == 1
    assert gdf.iloc[0]["collection"] == "ABI-L1b-RadF"


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_reads_collections(mock_s3_cls):
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    filename = "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc"
    path = f"noaa-goes16/ABI-L1b-RadF/2024/001/12/{filename}"
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: [{"name": path, "size": 1024 * 1024}] if p == prefix else []

    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        satellites=["GOES-16"],
    )
    gdf = plugin()

    assert not gdf.empty
    assert all(r["collection"] == "ABI-L1b-RadF" for _, r in gdf.iterrows())


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_filters_by_channels(mock_s3_cls):
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

    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        channels=["C01"],
    )
    gdf = plugin()

    assert len(gdf) == 1
    assert gdf.iloc[0]["channel_id"] == "1"
    assert "C01" in gdf.iloc[0]["href"]
    assert "id" in gdf.columns
    assert "start_time" in gdf.columns
    assert "end_time" in gdf.columns
    assert "geometry" in gdf.columns


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_aws_goes_filters_by_channel(mock_s3_cls):
    """When channels are set, only files matching those bands are returned."""
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
    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        channels=["C13"],
    )
    gdf = plugin()

    assert len(gdf) == 1
    assert "C13" in gdf.iloc[0]["href"]
    assert gdf.iloc[0]["channel_id"] == "13"


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_params_flow_through_to_s3fs(mock_s3_cls):
    """Credentials should be forwarded to s3fs.S3FileSystem."""
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []

    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        anon=True,
    )
    gdf = plugin()

    assert isinstance(gdf, gpd.GeoDataFrame)
    kwargs = mock_s3_cls.call_args.kwargs
    assert kwargs["anon"] is True


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_params_can_override_anon(mock_s3_cls):
    """Plugin should be able to override the default anon=True."""
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []

    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        anon=False,
        key="abc",
        secret="xyz",
    )
    gdf = plugin()

    assert isinstance(gdf, gpd.GeoDataFrame)
    kwargs = mock_s3_cls.call_args.kwargs
    assert kwargs["anon"] is False
    assert kwargs["key"] == "abc"
    assert kwargs["secret"] == "xyz"


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_no_collections_returns_empty(mock_s3_cls):
    """If collections is None or empty, return empty result."""
    plugin = SearchAwsGoes(
        collections=None,
        start_datetime=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc),
    )
    gdf = plugin()
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert gdf.empty


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_empty_collections_returns_empty(mock_s3_cls):
    plugin = SearchAwsGoes(
        collections=[],
        start_datetime=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc),
    )
    gdf = plugin()
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert gdf.empty


@pytest.mark.integration
@pytest.mark.slow
def test_search_aws_goes_real():
    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2024, 1, 1, 12, 1, tzinfo=timezone.utc),
    )
    gdf = plugin()

    assert not gdf.empty, "Expected to find GOES files on AWS for 2024-001 12:00"
    assert "href" in gdf.columns
    assert gdf.iloc[0]["collection"] == "ABI-L1b-RadF"


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_accepts_collections_mapping(mock_s3_cls):
    """A mapping of collection -> channels is accepted and filters results."""
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

    plugin = SearchAwsGoes(
        collections={"ABI-L1b-RadF": ["C01"]},
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        satellites=["GOES-16"],
    )
    gdf = plugin()

    assert len(gdf) == 1
    assert gdf.iloc[0]["channel_id"] == "1"


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_filters_by_intersects(mock_s3_cls):
    """When intersects is set, results outside the AOI are excluded."""
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)

    filename = "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc"
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"
    files = [{"name": f"{prefix}{filename}", "size": 1024 * 1024}]

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: files if p == prefix else []

    # AOI south of the GOES-East full disk footprint should return no results
    far_south_aoi = box(-180.0, -89.0, 180.0, -85.0)
    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        satellites=["GOES-16"],
        intersects=far_south_aoi,
    )
    gdf = plugin()

    assert gdf.empty


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_returns_all_channels_when_none_specified(mock_s3_cls):
    """When no channels are requested, all available bands are returned."""
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)

    filenames = [
        "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc",
        "OR_ABI-L1b-RadF-M6C02_G16_s20240011200000_e20240011209590_c20240011210000.nc",
        "OR_ABI-L1b-RadF-M6C13_G16_s20240011200000_e20240011209590_c20240011210000.nc",
    ]
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"
    files = [{"name": f"{prefix}{fn}", "size": 1024 * 1024} for fn in filenames]

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: files if p == prefix else []

    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        satellites=["GOES-16"],
    )
    gdf = plugin()

    assert len(gdf) == 3
    assert sorted(gdf["channel_id"].tolist()) == ["1", "13", "2"]


@patch("aereo.search_aws_goes.core.s3fs.S3FileSystem")
def test_search_goes19_bucket(mock_s3_cls):
    """GOES-19 maps to the noaa-goes19 bucket."""
    start_datetime = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end_datetime = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)

    filename = "OR_ABI-L1b-RadF-M6C01_G19_s20240011200000_e20240011209590_c20240011210000.nc"
    prefix = "noaa-goes19/ABI-L1b-RadF/2024/001/12/"
    files = [{"name": f"{prefix}{filename}", "size": 1024 * 1024}]

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: files if p == prefix else []

    plugin = SearchAwsGoes(
        collections=["ABI-L1b-RadF"],
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        satellites=["GOES-19"],
    )
    gdf = plugin()

    assert len(gdf) == 1
    assert gdf.iloc[0]["satellite"] == "GOES-19"
    assert "noaa-goes19" in gdf.iloc[0]["href"]
