"""Tests for function-based GOES search nodes."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import geopandas as gpd
from shapely.geometry import Polygon

from aereo.search_aws_goes.nodes import (
    SAT_TO_BUCKET,
    _build_hourly_steps,
    _build_s3fs,
    _empty_result,
    _normalize_collections,
    _resolve_channels,
    _resolve_satellites,
    search_assets,
    search_results,
    supported_collections,
)


# ---------------------------------------------------------------------------
# supported_collections
# ---------------------------------------------------------------------------


def test_supported_collections_is_tuple_of_products() -> None:
    assert isinstance(supported_collections, tuple)
    assert "ABI-L1b-RadF" in supported_collections
    assert "ABI-L2-CMIPF" in supported_collections


# ---------------------------------------------------------------------------
# _empty_result
# ---------------------------------------------------------------------------


def test_empty_result_returns_geodataframe() -> None:
    result = _empty_result()
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.empty
    assert "id" in result.columns
    assert "collection" in result.columns
    assert "href" in result.columns


# ---------------------------------------------------------------------------
# _normalize_collections
# ---------------------------------------------------------------------------


def test_normalize_collections_filters_unsupported() -> None:
    assert _normalize_collections(["ABI-L1b-RadF", "UNKNOWN"]) == ["ABI-L1b-RadF"]


def test_normalize_collections_empty() -> None:
    assert _normalize_collections([]) == []
    assert _normalize_collections(None) == []


# ---------------------------------------------------------------------------
# _build_hourly_steps
# ---------------------------------------------------------------------------


def test_build_hourly_steps_basic() -> None:
    start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 14, 0, tzinfo=timezone.utc)
    steps = _build_hourly_steps(start, end)
    assert len(steps) == 3
    assert steps[0].hour == 12
    assert steps[1].hour == 13
    assert steps[2].hour == 14


def test_build_hourly_steps_from_strings() -> None:
    steps = _build_hourly_steps("2024-01-01T12:00:00", "2024-01-01T12:00:00")
    assert len(steps) == 1
    assert steps[0].hour == 12


def test_build_hourly_steps_none() -> None:
    assert _build_hourly_steps(None, datetime(2024, 1, 1)) == []
    assert _build_hourly_steps(datetime(2024, 1, 1), None) == []


# ---------------------------------------------------------------------------
# _build_s3fs
# ---------------------------------------------------------------------------


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_build_s3fs_defaults(mock_s3_cls) -> None:
    _build_s3fs(None)
    kwargs = mock_s3_cls.call_args.kwargs
    assert kwargs["anon"] is True


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_build_s3fs_forwards_params(mock_s3_cls) -> None:
    _build_s3fs({"anon": False, "key": "abc", "satellite": "GOES-16"})
    kwargs = mock_s3_cls.call_args.kwargs
    assert kwargs["anon"] is False
    assert kwargs["key"] == "abc"
    assert "satellite" not in kwargs
    assert "channels" not in kwargs


# ---------------------------------------------------------------------------
# _resolve_satellites
# ---------------------------------------------------------------------------


def test_resolve_satellites_direct() -> None:
    assert _resolve_satellites("GOES-16", None) == {"GOES-16"}


def test_resolve_satellites_from_search_params() -> None:
    assert _resolve_satellites(None, {"satellite": "GOES-18"}) == {"GOES-18"}


def test_resolve_satellites_fallback() -> None:
    assert _resolve_satellites(None, None) == set(SAT_TO_BUCKET.keys())


# ---------------------------------------------------------------------------
# _resolve_channels
# ---------------------------------------------------------------------------


def test_resolve_channels_direct() -> None:
    assert _resolve_channels(["C01", "C13"], None) == {"1", "13"}


def test_resolve_channels_from_search_params() -> None:
    assert _resolve_channels(None, {"channels": ["C02"]}) == {"2"}


def test_resolve_channels_none() -> None:
    assert _resolve_channels(None, None) is None


# ---------------------------------------------------------------------------
# search_assets
# ---------------------------------------------------------------------------


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_search_assets_empty_collections(mock_s3_cls) -> None:
    result = search_assets(collections=[])
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.empty


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_search_assets_none_collections(mock_s3_cls) -> None:
    result = search_assets(collections=None)
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.empty


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_search_assets_no_results(mock_s3_cls) -> None:
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []

    start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    result = search_assets(
        collections=["ABI-L1b-RadF"],
        start_datetime=start,
        end_datetime=end,
    )
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.empty


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_search_assets_returns_geodataframe(mock_s3_cls) -> None:
    start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    filename = "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc"
    path = f"noaa-goes16/ABI-L1b-RadF/2024/001/12/{filename}"
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: [{"name": path, "size": 1024 * 1024}] if p == prefix else []

    result = search_assets(
        collections=["ABI-L1b-RadF"],
        start_datetime=start,
        end_datetime=end,
    )

    assert isinstance(result, gpd.GeoDataFrame)
    assert not result.empty
    assert len(result) == 1
    assert result.iloc[0]["collection"] == "ABI-L1b-RadF"
    assert result.iloc[0]["href"] == f"s3://{path}"


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_search_assets_filters_by_channels(mock_s3_cls) -> None:
    start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)

    filenames = [
        "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc",
        "OR_ABI-L1b-RadF-M6C02_G16_s20240011200000_e20240011209590_c20240011210000.nc",
    ]
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"
    files = [{"name": f"{prefix}{fn}", "size": 1024 * 1024} for fn in filenames]

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: files if p == prefix else []

    result = search_assets(
        collections=["ABI-L1b-RadF"],
        start_datetime=start,
        end_datetime=end,
        channels=["C01"],
    )

    assert len(result) == 1
    assert result.iloc[0]["channel_id"] == "1"


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_search_assets_with_satellite(mock_s3_cls) -> None:
    start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    filename = "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc"
    path = f"noaa-goes16/ABI-L1b-RadF/2024/001/12/{filename}"
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"

    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.side_effect = lambda p, detail=False: [{"name": path, "size": 1024 * 1024}] if p == prefix else []

    result = search_assets(
        collections=["ABI-L1b-RadF"],
        start_datetime=start,
        end_datetime=end,
        satellite="GOES-16",
    )

    assert not result.empty
    assert all(r["satellite"] == "GOES-16" for _, r in result.iterrows())


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_search_params_flow_through_to_s3fs(mock_s3_cls) -> None:
    start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []

    result = search_assets(
        collections=["ABI-L1b-RadF"],
        start_datetime=start,
        end_datetime=end,
        search_params={"requester_pays": True},
    )

    assert isinstance(result, gpd.GeoDataFrame)
    kwargs = mock_s3_cls.call_args.kwargs
    assert kwargs["anon"] is True
    assert kwargs["requester_pays"] is True


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_search_params_can_override_anon(mock_s3_cls) -> None:
    start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []

    result = search_assets(
        collections=["ABI-L1b-RadF"],
        start_datetime=start,
        end_datetime=end,
        search_params={"anon": False, "key": "abc", "secret": "xyz"},
    )

    assert isinstance(result, gpd.GeoDataFrame)
    kwargs = mock_s3_cls.call_args.kwargs
    assert kwargs["anon"] is False
    assert kwargs["key"] == "abc"
    assert kwargs["secret"] == "xyz"


@patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem")
def test_search_assets_skips_unsupported_collections(mock_s3_cls) -> None:
    start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    mock_fs = mock_s3_cls.return_value
    mock_fs.ls.return_value = []

    result = search_assets(
        collections=["UNKNOWN-PRODUCT"],
        start_datetime=start,
        end_datetime=end,
    )
    assert isinstance(result, gpd.GeoDataFrame)
    assert result.empty


# ---------------------------------------------------------------------------
# search_results
# ---------------------------------------------------------------------------


def test_search_results_passthrough() -> None:
    gdf = gpd.GeoDataFrame(
        {"id": ["1"], "collection": ["A"]},
        geometry=[Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
    )
    assert search_results(gdf) is gdf


# ---------------------------------------------------------------------------
# Hamilton integration
# ---------------------------------------------------------------------------


def test_search_pipeline_runs() -> None:
    """Build a real Hamilton driver from the nodes module and execute it."""
    from hamilton import driver

    from aereo.search_aws_goes import nodes as search_module

    start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 12, 10, tzinfo=timezone.utc)
    filename = "OR_ABI-L1b-RadF-M6C01_G16_s20240011200000_e20240011209590_c20240011210000.nc"
    path = f"noaa-goes16/ABI-L1b-RadF/2024/001/12/{filename}"
    prefix = "noaa-goes16/ABI-L1b-RadF/2024/001/12/"

    with patch("aereo.search_aws_goes.nodes.s3fs.S3FileSystem") as mock_s3_cls:
        mock_fs = mock_s3_cls.return_value
        mock_fs.ls.side_effect = lambda p, detail=False: [{"name": path, "size": 1024 * 1024}] if p == prefix else []

        dr = driver.Builder().with_modules(search_module).build()
        result = dr.execute(
            ["search_results"],
            inputs={
                "aoi": None,
                "start_datetime": start,
                "end_datetime": end,
                "collections": ["ABI-L1b-RadF"],
            },
        )

    assert "search_results" in result
    gdf = result["search_results"]
    assert isinstance(gdf, gpd.GeoDataFrame)
    assert not gdf.empty
    assert gdf.iloc[0]["collection"] == "ABI-L1b-RadF"
