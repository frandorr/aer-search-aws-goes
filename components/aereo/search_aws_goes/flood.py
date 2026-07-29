"""Search provider for the NOAA GOES ABI flood products on AWS S3.

Lists the ``ABI-Flood-DCOM`` (daily composite) and ``ABI-Flood-HCOM``
(hourly composite) GeoTIFFs published on the public GOES buckets
(``ABI-Flood-Day-TIF`` / ``ABI-Flood-Hourly-TIF`` prefixes) and returns them
as an :class:`aereo.schemas.AssetSchema` GeoDataFrame, mirroring the
:func:`aereo.search_aws_goes.core.search_aws_goes` interface.

Unlike the standard ABI products (partitioned by ``YYYY/DOY/HH`` and
filtered by channel), the flood composites are partitioned by
``YYYY/MM/DD`` and tiled by AOI identifier (e.g. ``AOI004``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import s3fs
from aereo.interfaces import (
    build_collection_asset_filters,
    empty_asset_result,
    normalize_geometry_input,
)
from aereo.schemas import AssetSchema
from aereo.search_aws_goes.core import SAT_TO_BUCKET
from pandera.typing.geopandas import GeoDataFrame
from pydantic import ConfigDict, validate_call
from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry
from structlog import get_logger

logger = get_logger()

SUPPORTED_FLOOD_PRODUCTS = [
    "ABI-Flood-Day-TIF",
    "ABI-Flood-Hourly-TIF",
]

FLOOD_FILENAME_RE = re.compile(
    r"ABI-Flood-(?P<kind>DCOM|HCOM)-(?P<aoi>AOI\d+)_(?P<version>v\w+)_g(?P<sat>\d+)"
    r"_s(?P<start>\d{14})\d_e(?P<end>\d{14})\d_c(?P<created>\d{14})\d\.tif"
)

#: Cache of AOI footprints keyed by ``(bucket, aoi_id)``. AOI regions are
#: fixed, so bounds are read once from the first file seen and reused.
_AOI_GEOMETRY_CACHE: dict[tuple[str, str], Polygon] = {}


def _parse_timestamp(value: str) -> datetime:
    """Parse a ``YYYYmmddHHMMSS`` UTC timestamp from a flood filename."""
    return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def _parse_goes_flood_filename(filename: str) -> dict[str, Any]:
    """Parse metadata from a GOES ABI flood product filename.

    Example: ``ABI-Flood-DCOM-AOI002_v1r1_g19_s202511031130211_e202511032329517_c202511040700036.tif``

    Unlike the standard GOES-R naming (day-of-year timestamps), the flood
    products use ``YYYYmmddHHMMSS`` plus a fractional-second digit.
    """
    match = FLOOD_FILENAME_RE.search(filename)
    if not match:
        return {}

    try:
        start_time = _parse_timestamp(match.group("start"))
        end_time = _parse_timestamp(match.group("end"))
    except ValueError:
        return {}

    return {
        "aoi_id": match.group("aoi"),
        "satellite": f"GOES-{int(match.group('sat'))}",
        "start_time": start_time,
        "end_time": end_time,
    }


def _normalize_aoi(aoi: str) -> str:
    """Normalize an AOI identifier to the ``AOINNN`` form.

    Accepts inputs like ``"AOI002"``, ``"aoi2"`` or ``"2"`` and returns
    ``"AOI002"``.
    """
    aoi_str = str(aoi).upper()
    aoi_str = aoi_str.removeprefix("AOI")
    try:
        return f"AOI{int(aoi_str):03d}"
    except ValueError:
        return str(aoi)


def _get_aoi_geometry(bucket: str, aoi_id: str, https_url: str) -> Polygon | None:
    """Return the WGS84 footprint of an AOI, reading raster bounds once.

    The flood GeoTIFFs are plain EPSG:4326 rasters, so the file bounds are
    used directly as the AOI footprint. Results are cached per
    ``(bucket, aoi_id)``; returns ``None`` when the raster cannot be opened.
    """
    key = (bucket, aoi_id)
    if key in _AOI_GEOMETRY_CACHE:
        return _AOI_GEOMETRY_CACHE[key]

    import rasterio
    from rasterio.errors import RasterioIOError

    try:
        with rasterio.open(https_url) as ds:
            b = ds.bounds
            geometry = box(b.left, b.bottom, b.right, b.top)
    except RasterioIOError as e:
        logger.warning("Could not read AOI footprint", bucket=bucket, aoi_id=aoi_id, error=str(e))
        return None

    _AOI_GEOMETRY_CACHE[key] = geometry
    return geometry


@validate_call(config=ConfigDict(arbitrary_types_allowed=True))
def search_aws_goes_flood(
    collections: Mapping[str, Sequence[str]] | Sequence[str] | None,
    intersects: BaseGeometry | dict[str, Any] | str | Path | None,
    start_datetime: datetime | None,
    end_datetime: datetime | None,
    aois: list[str] | None = None,
    satellites: list[str] | None = None,
) -> GeoDataFrame[AssetSchema]:
    """Search for GOES ABI flood products on AWS S3.

    Traverses the public NOAA GOES S3 buckets
    (``noaa-goes16``, ``noaa-goes17``, ``noaa-goes18``, ``noaa-goes19``)
    by year/month/day prefix and returns matching GeoTIFF assets as a
    validated GeoDataFrame.

    Args:
        collections: Flood product collections to search
            (``ABI-Flood-Day-TIF`` and/or ``ABI-Flood-Hourly-TIF``). When a
            mapping is given, its values filter AOI identifiers
            (e.g. ``{"ABI-Flood-Day-TIF": ["AOI003"]}``).
        intersects: AOI geometry for spatial filtering.
        start_datetime: Start of temporal window.
        end_datetime: End of temporal window. A midnight value (e.g. a
            date-only string like ``"2025-11-03"``) is inclusive of the whole
            day, mirroring STAC date semantics.
        aois: Optional AOI identifiers to filter (e.g. ``["AOI003"]``).
        satellites: Optional satellites to include (default: all).

    Returns:
        A GeoDataFrame where each row represents a matched flood granule
        with columns defined by :class:`aereo.schemas.AssetSchema`.
    """
    collections, asset_filters = build_collection_asset_filters(collections)
    if not collections:
        return empty_asset_result()

    supported_set = set(SUPPORTED_FLOOD_PRODUCTS)
    normalized_collections = []
    for col in collections:
        if col in supported_set:
            normalized_collections.append(col)
        else:
            logger.warning("Skipping unsupported collection", collection=col)

    if not normalized_collections:
        return empty_asset_result()

    if not start_datetime or not end_datetime:
        return empty_asset_result()

    fs = s3fs.S3FileSystem(anon=True)

    requested_satellites = set(satellites) if satellites else set(SAT_TO_BUCKET.keys())

    requested_aois: set[str] | None = set()
    if aois:
        for aoi in aois:
            requested_aois.add(_normalize_aoi(aoi))
    for col in normalized_collections:
        col_aois = asset_filters.get(col)
        if col_aois:
            for aoi in col_aois:
                requested_aois.add(_normalize_aoi(aoi))
    if not requested_aois:
        requested_aois = None

    q_start = start_datetime
    if q_start.tzinfo is None:
        q_start = q_start.replace(tzinfo=UTC)
    q_end = end_datetime
    if q_end.tzinfo is None:
        q_end = q_end.replace(tzinfo=UTC)

    # A midnight end (e.g. a date-only string like "2025-11-03") means the
    # whole day, mirroring STAC date semantics.
    if q_end.time() == time.min:
        q_end += timedelta(days=1)

    search_start = q_start.replace(hour=0, minute=0, second=0, microsecond=0)

    daily_steps = []
    current_day = search_start
    while current_day < q_end:
        daily_steps.append(current_day)
        current_day += timedelta(days=1)

    rows: list[dict[str, Any]] = []

    geom = normalize_geometry_input(intersects)

    for collection in normalized_collections:
        for satellite in requested_satellites:
            bucket = SAT_TO_BUCKET.get(satellite)
            if not bucket:
                continue

            for d in daily_steps:
                prefix = f"{bucket}/{collection}/{d.year}/{d.strftime('%m')}/{d.strftime('%d')}/"
                try:
                    files = fs.ls(prefix, detail=True)
                    for f_info in files:
                        f_path = f_info["name"]
                        if not f_path.endswith(".tif"):
                            continue

                        filename = f_path.split("/")[-1]
                        meta = _parse_goes_flood_filename(filename)

                        if not meta:
                            continue

                        if meta["start_time"] > q_end or meta["end_time"] < q_start:
                            continue

                        if requested_aois is not None and meta["aoi_id"] not in requested_aois:
                            continue

                        https_url = f"https://{bucket}.s3.amazonaws.com/{f_path.replace(bucket + '/', '')}"
                        geometry = _get_aoi_geometry(bucket, meta["aoi_id"], https_url)
                        if geom is not None and geometry is not None and not geom.intersects(geometry):
                            continue

                        granule_id = Path(filename).stem

                        rows.append(
                            {
                                "id": granule_id,
                                "collection": collection,
                                "geometry": geometry,
                                "start_time": meta["start_time"],
                                "end_time": meta["end_time"],
                                "href": f"s3://{f_path}",
                                "https_url": https_url,
                                "size_mb": f_info["size"] / (1024 * 1024),
                                "aoi_id": meta["aoi_id"],
                                "granule_id": granule_id,
                                "satellite": satellite,
                            }
                        )

                except FileNotFoundError as e:
                    logger.debug("S3 prefix not found", prefix=prefix, error=str(e))

    if not rows:
        return empty_asset_result()

    gdf = gpd.GeoDataFrame(rows, geometry="geometry")
    return cast(GeoDataFrame, AssetSchema.validate(gdf))
