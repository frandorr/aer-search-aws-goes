from datetime import datetime, timedelta, timezone
import re
from typing import Any

import s3fs
import geopandas as gpd
from structlog import get_logger

from aer.plugin import plugin
from aer.search import SearchQuery, SearchResultSchema
from aer.spectral import Channel, Product
from pandera.typing.geopandas import GeoDataFrame

logger = get_logger()


def _parse_goes_filename(filename: str) -> dict[str, Any]:
    """Parse start/end times and band channel ID from a GOES-R filename.

    Example: OR_ABI-L1b-RadF-M6C01_G16_s202312312345678_e202312312354567_c202312312355432.nc

    The channel ID is extracted from the ``C##`` portion (e.g. ``"1"`` from
    ``C01``, ``"13"`` from ``C13``).
    """
    match = re.search(r"_s(\d{13})\d*_e(\d{13})\d*_c(\d{13})\d*\.nc", filename)
    if not match:
        return {}

    start_str = match.group(1)
    end_str = match.group(2)

    try:
        start_time = datetime.strptime(start_str, "%Y%j%H%M%S").replace(
            tzinfo=timezone.utc
        )
        end_time = datetime.strptime(end_str, "%Y%j%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return {}

    # Extract band/channel ID from the filename (e.g. C01 → "1", C13 → "13")
    band_match = re.search(r"-M\d+C(\d+)", filename)
    channel_id = str(int(band_match.group(1))) if band_match else None

    return {
        "start_time": start_time,
        "end_time": end_time,
        "channel_id": channel_id,
    }


def _find_channel_by_id(
    product_channels: tuple[Channel, ...], c_id: str
) -> Channel | None:
    """Look up the Channel object matching a channel ID from a product's channel list."""
    for ch in product_channels:
        if ch.c_id == c_id:
            return ch
    return None

VALID_PRODUCTS = [Product.get(name) for name in ["ABI-L1b-RadF", "ABI-L1b-RadC", "ABI-L1b-RadM"]]

def _all_valid_products(products: list[Product]):
    return all(p in VALID_PRODUCTS for p in products)


@plugin(name="aws_goes", category="search")
def search_aws_goes(query: SearchQuery) -> GeoDataFrame["SearchResultSchema"]:
    """Search for GOES ABI products on AWS S3.

    This plugin traverses the NOAA GOES S3 buckets (noaa-goes16, noaa-goes17, etc.)
    by year/day/hour based on the requested time range.

    When ``query.channels`` is set, only files matching the requested bands are
    returned.  Each result row includes a ``channels`` column containing the
    matching :class:`Channel` as a single-element tuple.

    .. note::
        This plugin assumes that the input ``spatial_extent`` is between bounds
        for GOES satellite projection.
    """
    # check if any of the products are valid
    if not _all_valid_products(query.products):
        raise ValueError("Invalid product in query {}".format(query.products))

    fs = s3fs.S3FileSystem(anon=True)
    rows = []

    sat_to_bucket = {
        "GOES-16": "noaa-goes16",
        "GOES-17": "noaa-goes17",
        "GOES-18": "noaa-goes18",
        "GOES-19": "noaa-goes19",
    }

    # Build a set of requested channel IDs for fast lookup
    requested_channel_ids: set[str] | None = None
    if query.channels:
        requested_channel_ids = {ch.c_id for ch in query.channels}

    # Generate hourly prefixes to scan
    search_start = query.time_range.start.replace(minute=0, second=0, microsecond=0)
    search_end = query.time_range.end

    # Ensure timezone awareness for comparisons against S3 file metadata
    q_start = query.time_range.start.replace(tzinfo=timezone.utc) if query.time_range.start.tzinfo is None else query.time_range.start
    q_end = query.time_range.end.replace(tzinfo=timezone.utc) if query.time_range.end.tzinfo is None else query.time_range.end

    current_hour = search_start
    hourly_steps = []
    while current_hour <= search_end:
        hourly_steps.append(current_hour)
        current_hour += timedelta(hours=1)

    for product in query.products:
        # Only support ABI L1b for now
        if not product.name.startswith("ABI-L1b-Rad"):
            continue

        if query.satellites:
            requested_satellites = query.satellites
        else:
            requested_satellites = product.supported_satellites

        for satellite in requested_satellites:
            bucket = sat_to_bucket.get(satellite.name)
            if not bucket:
                continue

            for h in hourly_steps:
                # AWS path: <product>/<year>/<day>/<hour>/
                prefix = f"{bucket}/{product.name}/{h.year}/{h.strftime('%j')}/{h.strftime('%H')}/"
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

                        # Filter by exact time range
                        if (
                            meta["start_time"] > q_end
                            or meta["end_time"] < q_start
                        ):
                            continue

                        # Filter by channel if requested
                        file_channel_id = meta.get("channel_id")
                        if (
                            requested_channel_ids is not None
                            and file_channel_id not in requested_channel_ids
                        ):
                            continue

                        # Resolve the Channel object for this file
                        file_channel = None
                        if file_channel_id:
                            file_channel = _find_channel_by_id(product.channels, file_channel_id)
                            if file_channel is None:
                                logger.warning(
                                    "Channel ID found in filename but missing from product channels",
                                    channel_id=file_channel_id,
                                    product=product.name,
                                    filename=filename,
                                )

                        rows.append(
                            {
                                "product_name": product.name,
                                "granule_id": filename,
                                "start_time": meta["start_time"],
                                "end_time": meta["end_time"],
                                "s3_url": f"s3://{f_path}",
                                "https_url": f"https://{bucket}.s3.amazonaws.com/{f_path.replace(bucket + '/', '')}",
                                "size_mb": f_info["size"] / (1024 * 1024),
                                "channels": (file_channel,) if file_channel else (),
                                "geometry": None,
                                "input_spatial_extent": query.spatial_extent,
                                "overlapping_spatial_extent": query.spatial_extent,
                                "cell_overlap_mode": query.cell_overlap_mode,
                            }
                        )
                except FileNotFoundError as e:
                    logger.debug(
                        "S3 prefix not found", prefix=prefix, error=str(e)
                    )

    if not rows:
        gdf = gpd.GeoDataFrame(
            columns=[
                "product_name",
                "granule_id",
                "start_time",
                "end_time",
                "s3_url",
                "https_url",
                "size_mb",
                "channels",
                "geometry",
                "input_spatial_extent",
                "overlapping_spatial_extent",
                "cell_overlap_mode",
            ],
            geometry="geometry",
        )
        return SearchResultSchema.validate(gdf)

    gdf = gpd.GeoDataFrame(rows, geometry="geometry")
    return SearchResultSchema.validate(gdf)
