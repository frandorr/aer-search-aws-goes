"""Function-based Hamilton nodes for NOAA GOES-R ABI search on AWS S3.

These nodes replace the class-based :class:`AwsGoesSearchPlugin` with plain
functions that Hamilton can compose into a DAG.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import s3fs
from pandera.typing.geopandas import GeoDataFrame
from shapely.geometry.base import BaseGeometry
from structlog import get_logger

from aereo.schemas import AssetSchema

from .core import SUPPORTED_PRODUCTS
from .utils import (
    _get_geometry,
    _parse_domain,
    _parse_goes_filename,
)

logger = get_logger()

# Module-level variable consumed by the plugin discovery machinery.
supported_collections = tuple(SUPPORTED_PRODUCTS)

SAT_TO_BUCKET = {
    "GOES-16": "noaa-goes16",
    "GOES-17": "noaa-goes17",
    "GOES-18": "noaa-goes18",
    "GOES-19": "noaa-goes19",
}


def _empty_result() -> GeoDataFrame:
    """Return an empty validated GeoDataFrame with AssetSchema columns."""
    columns = list(AssetSchema.to_schema().columns.keys())
    if "geometry" not in columns:
        columns.append("geometry")
    gdf = gpd.GeoDataFrame(columns=columns, geometry="geometry")
    return cast(GeoDataFrame, AssetSchema.validate(gdf))


def _normalize_collections(collections: Sequence[str] | None) -> list[str]:
    """Filter user-supplied collections against supported products."""
    if not collections:
        return []
    supported_set = set(SUPPORTED_PRODUCTS)
    normalized: list[str] = []
    for col in collections:
        if col in supported_set:
            normalized.append(col)
        else:
            logger.warning("skipping_unsupported_collection", collection=col)
    return normalized


def _build_hourly_steps(
    start_datetime: datetime | str | None,
    end_datetime: datetime | str | None,
) -> list[datetime]:
    """Build a list of hourly datetime steps between start and end."""
    if start_datetime is None or end_datetime is None:
        return []

    if isinstance(start_datetime, str):
        start_dt = datetime.fromisoformat(start_datetime.replace("Z", "+00:00"))
    else:
        start_dt = start_datetime

    if isinstance(end_datetime, str):
        end_dt = datetime.fromisoformat(end_datetime.replace("Z", "+00:00"))
    else:
        end_dt = end_datetime

    search_start = start_dt.replace(minute=0, second=0, microsecond=0)
    search_end = end_dt

    if search_start.tzinfo is None:
        search_start = search_start.replace(tzinfo=timezone.utc)
    if search_end.tzinfo is None:
        search_end = search_end.replace(tzinfo=timezone.utc)

    hourly_steps: list[datetime] = []
    current_hour = search_start
    while current_hour <= search_end:
        hourly_steps.append(current_hour)
        current_hour += timedelta(hours=1)
    return hourly_steps


def _build_s3fs(search_params: Mapping[str, Any] | None) -> s3fs.S3FileSystem:
    """Build an s3fs.S3FileSystem from search_params."""
    if search_params is None:
        search_params = {}
    fs_kwargs = dict(search_params)
    fs_kwargs.pop("satellite", None)
    fs_kwargs.pop("channels", None)
    if "anon" not in fs_kwargs:
        fs_kwargs["anon"] = True
    return s3fs.S3FileSystem(**fs_kwargs)


def _resolve_satellites(
    satellite: str | None,
    search_params: Mapping[str, Any] | None,
) -> set[str]:
    """Resolve the set of satellites to search."""
    if satellite is not None:
        return {satellite.upper()}
    if search_params and "satellite" in search_params:
        return {str(search_params["satellite"]).upper()}
    return set(SAT_TO_BUCKET.keys())


def _resolve_channels(
    channels: Sequence[str] | None,
    search_params: Mapping[str, Any] | None,
) -> set[str] | None:
    """Resolve the set of channel IDs to filter by."""
    raw_channels = None
    if channels is not None:
        raw_channels = channels
    elif search_params and "channels" in search_params:
        raw_channels = search_params["channels"]

    if raw_channels is None:
        return None

    profile_channels: set[str] = set()
    for ch in raw_channels:
        ch_str = str(ch).upper()
        if ch_str.startswith("C"):
            ch_str = ch_str[1:]
        try:
            profile_channels.add(str(int(ch_str)))
        except ValueError:
            profile_channels.add(str(ch))
    return profile_channels if profile_channels else None


def search_assets(
    aoi: BaseGeometry | None = None,
    start_datetime: datetime | str | None = None,
    end_datetime: datetime | str | None = None,
    collections: Sequence[str] | None = None,
    channels: Sequence[str] | None = None,
    satellite: str | None = None,
    search_params: Mapping[str, Any] | None = None,
) -> GeoDataFrame:
    """Search for GOES ABI products on AWS S3.

    Traverses the public NOAA GOES S3 buckets by year/day/hour prefix and
    returns matching NetCDF assets as a validated GeoDataFrame.

    Args:
        aoi: Optional geometry for spatial filtering. Currently unused because
            GOES domain geometry is derived from the product name.
        start_datetime: Inclusive start of the temporal query range.
        end_datetime: Inclusive end of the temporal query range.
        collections: Sequence of GOES product names (e.g. ``ABI-L1b-RadF``).
        channels: Optional sequence of channel IDs to filter by (e.g.
            ``["C01", "C13"]`` or ``["1", "13"]``).
        satellite: Optional satellite identifier (e.g. ``"GOES-16"``).
        search_params: Additional kwargs forwarded to ``s3fs.S3FileSystem``
            (e.g. ``anon``, ``key``, ``secret``).

    Returns:
        A GeoDataFrame where each row represents a matched GOES granule with
        columns defined by :class:`aereo.schemas.AssetSchema`.
    """
    del aoi  # unused — GOES domain geometry is derived from the product name

    normalized_collections = _normalize_collections(collections)
    if not normalized_collections:
        return _empty_result()

    hourly_steps = _build_hourly_steps(start_datetime, end_datetime)
    if not hourly_steps:
        return _empty_result()

    fs = _build_s3fs(search_params)
    requested_satellites = _resolve_satellites(satellite, search_params)
    requested_channel_ids = _resolve_channels(channels, search_params)

    q_start = hourly_steps[0]
    q_end = hourly_steps[-1] + timedelta(hours=1)

    rows: list[dict[str, Any]] = []

    for collection in normalized_collections:
        for sat in requested_satellites:
            bucket = SAT_TO_BUCKET.get(sat)
            if not bucket:
                continue

            for h in hourly_steps:
                prefix = f"{bucket}/{collection}/{h.year}/{h.strftime('%j')}/{h.strftime('%H')}/"
                try:
                    files = fs.ls(prefix, detail=True)
                    for f_info in files:
                        f_path = f_info["name"]
                        if not f_path.endswith(".nc"):
                            continue

                        filename = f_path.split("/")[-1]
                        meta = _parse_goes_filename(filename)

                        if not meta:
                            continue

                        if meta["start_time"] > q_end or meta["end_time"] < q_start:
                            continue

                        file_channel_id = meta.get("channel_id")
                        if requested_channel_ids is not None and file_channel_id not in requested_channel_ids:
                            continue

                        domain = _parse_domain(collection)
                        geometry = _get_geometry(sat, domain)
                        granule_id = Path(filename).stem

                        rows.append(
                            {
                                "id": granule_id,
                                "collection": collection,
                                "geometry": geometry,
                                "start_time": meta["start_time"],
                                "end_time": meta["end_time"],
                                "href": f"s3://{f_path}",
                                "https_url": (f"https://{bucket}.s3.amazonaws.com/{f_path.replace(bucket + '/', '')}"),
                                "size_mb": f_info["size"] / (1024 * 1024),
                                "channel_id": file_channel_id,
                                "granule_id": granule_id,
                                "satellite": sat,
                                "domain": domain,
                            }
                        )
                except FileNotFoundError as exc:
                    logger.debug("s3_prefix_not_found", prefix=prefix, error=str(exc))

    if not rows:
        return _empty_result()

    gdf = gpd.GeoDataFrame(rows, geometry="geometry")
    return cast(GeoDataFrame, AssetSchema.validate(gdf))


def search_results(search_assets: GeoDataFrame) -> GeoDataFrame:
    """Return validated search results.

    This is the output boundary of the search stage. Downstream Hamilton
    nodes depend on ``search_results`` so that the plugin can be swapped
    without changing the DAG contract.
    """
    return search_assets
