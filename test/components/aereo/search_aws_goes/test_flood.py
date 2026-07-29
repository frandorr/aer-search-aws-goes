from datetime import UTC, datetime
from unittest.mock import patch

import geopandas as gpd
from aereo.search_aws_goes import search_aws_goes_flood
from aereo.search_aws_goes.flood import _normalize_aoi, _parse_goes_flood_filename
from shapely.geometry import box


def test_parse_goes_flood_filename_daily():
    meta = _parse_goes_flood_filename(
        "ABI-Flood-DCOM-AOI002_v1r1_g19_s202511031130211_e202511032329517_c202511040700036.tif"
    )
    assert meta["aoi_id"] == "AOI002"
    assert meta["satellite"] == "GOES-19"
    assert meta["start_time"] == datetime(2025, 11, 3, 11, 30, 21, tzinfo=UTC)
    assert meta["end_time"] == datetime(2025, 11, 3, 23, 29, 51, tzinfo=UTC)


def test_parse_goes_flood_filename_hourly():
    meta = _parse_goes_flood_filename(
        "ABI-Flood-HCOM-AOI004_v1r1_g16_s202001020059599_e202001020159599_c202001020300018.tif"
    )
    assert meta["aoi_id"] == "AOI004"
    assert meta["satellite"] == "GOES-16"
    assert meta["start_time"] == datetime(2020, 1, 2, 0, 59, 59, tzinfo=UTC)


def test_parse_goes_flood_filename_invalid():
    assert _parse_goes_flood_filename("OR_ABI-L1b-RadF-M6C01_G16_s202312312345678.nc") == {}
    assert _parse_goes_flood_filename("random_file.tif") == {}


def test_normalize_aoi():
    assert _normalize_aoi("AOI002") == "AOI002"
    assert _normalize_aoi("aoi2") == "AOI002"
    assert _normalize_aoi("2") == "AOI002"
    assert _normalize_aoi("13") == "AOI013"


def test_search_no_collections_returns_empty():
    result = search_aws_goes_flood(
        collections=None,
        intersects=None,
        start_datetime=datetime(2025, 11, 3, tzinfo=UTC),
        end_datetime=datetime(2025, 11, 4, tzinfo=UTC),
    )
    assert len(result) == 0


def test_search_unsupported_collection_returns_empty():
    result = search_aws_goes_flood(
        collections=["ABI-L1b-RadC"],
        intersects=None,
        start_datetime=datetime(2025, 11, 3, tzinfo=UTC),
        end_datetime=datetime(2025, 11, 4, tzinfo=UTC),
    )
    assert len(result) == 0


def test_search_missing_datetimes_returns_empty():
    result = search_aws_goes_flood(
        collections=["ABI-Flood-Day-TIF"],
        intersects=None,
        start_datetime=None,
        end_datetime=None,
    )
    assert len(result) == 0


@patch("aereo.search_aws_goes.flood._get_aoi_geometry")
@patch("aereo.search_aws_goes.flood.s3fs.S3FileSystem")
def test_search_aws_goes_flood_matches_assets(mock_s3_cls, mock_get_geometry):
    mock_fs = mock_s3_cls.return_value
    filename = "ABI-Flood-DCOM-AOI004_v1r1_g19_s202511031130211_e202511032329517_c202511040700036.tif"
    mock_fs.ls.return_value = [
        {"name": f"noaa-goes19/ABI-Flood-Day-TIF/2025/11/03/{filename}", "size": 1024 * 1024},
        {"name": "noaa-goes19/ABI-Flood-Day-TIF/2025/11/03/not_a_flood.tif", "size": 10},
    ]
    mock_get_geometry.return_value = box(-65, -35, -60, -30)

    gdf = search_aws_goes_flood(
        collections={"ABI-Flood-Day-TIF": ["AOI004"]},
        intersects=box(-64, -34, -61, -31),
        start_datetime=datetime(2025, 11, 3, tzinfo=UTC),
        end_datetime=datetime(2025, 11, 3, tzinfo=UTC),
        satellites=["GOES-19"],
    )

    assert isinstance(gdf, gpd.GeoDataFrame)
    assert len(gdf) == 1
    row = gdf.iloc[0]
    assert row["collection"] == "ABI-Flood-Day-TIF"
    assert row["aoi_id"] == "AOI004"
    assert row["satellite"] == "GOES-19"
    assert row["href"] == f"s3://noaa-goes19/ABI-Flood-Day-TIF/2025/11/03/{filename}"
    assert row["https_url"] == f"https://noaa-goes19.s3.amazonaws.com/ABI-Flood-Day-TIF/2025/11/03/{filename}"


@patch("aereo.search_aws_goes.flood._get_aoi_geometry")
@patch("aereo.search_aws_goes.flood.s3fs.S3FileSystem")
def test_search_aws_goes_flood_filters_by_aoi(mock_s3_cls, mock_get_geometry):
    mock_fs = mock_s3_cls.return_value
    filename = "ABI-Flood-DCOM-AOI002_v1r1_g19_s202511031130211_e202511032329517_c202511040700036.tif"
    mock_fs.ls.return_value = [
        {"name": f"noaa-goes19/ABI-Flood-Day-TIF/2025/11/03/{filename}", "size": 1024},
    ]
    mock_get_geometry.return_value = box(-65, -35, -60, -30)

    gdf = search_aws_goes_flood(
        collections={"ABI-Flood-Day-TIF": ["AOI004"]},
        intersects=None,
        start_datetime=datetime(2025, 11, 3, tzinfo=UTC),
        end_datetime=datetime(2025, 11, 4, tzinfo=UTC),
        satellites=["GOES-19"],
    )

    assert gdf.empty
