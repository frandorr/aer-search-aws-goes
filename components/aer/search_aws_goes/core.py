from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any

import s3fs
import geopandas as gpd
from shapely.geometry import Polygon
from structlog import get_logger

from aer.plugin.core import hookimpl, SearchResultSchema
from aer.spatial import GeomLike
from aer.temporal import TimeRange
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


class AwsGoesSearchPlugin:
    @hookimpl
    def search(
        self,
        collections: list[str],
        intersects: GeomLike | None,
        time_range: TimeRange | None,
        search_params: dict | None,
    ) -> GeoDataFrame["SearchResultSchema"]:
        """Search for GOES ABI products on AWS S3.

        This plugin traverses the NOAA GOES S3 buckets (noaa-goes16, noaa-goes17, etc.)
        by year/day/hour based on the requested time range.

        When channel filters are provided via search_params["channels"],
        only files matching those bands are returned.
        """
        if search_params is None:
            search_params = {}

        fs = s3fs.S3FileSystem(anon=True)
        rows: list[dict[str, Any]] = []

        sat_to_bucket = {
            "GOES-16": "noaa-goes16",
            "GOES-17": "noaa-goes17",
            "GOES-18": "noaa-goes18",
            "GOES-19": "noaa-goes19",
        }

        requested_channel_ids: set[str] | None = None
        if "channels" in search_params:
            requested_channel_ids = set()
            for ch in search_params["channels"]:
                ch_str = str(ch).upper()
                if ch_str.startswith("C"):
                    ch_str = ch_str[1:]
                try:
                    requested_channel_ids.add(str(int(ch_str)))
                except ValueError:
                    requested_channel_ids.add(str(ch))

        requested_satellites: list[str] | set[str] = list(sat_to_bucket.keys())
        if "satellites" in search_params:
            requested_satellites = set(search_params["satellites"])

        if not time_range:
            return self._empty_result()

        search_start = time_range.start.replace(minute=0, second=0, microsecond=0)
        search_end = time_range.end

        q_start = (
            time_range.start.replace(tzinfo=timezone.utc)
            if time_range.start.tzinfo is None
            else time_range.start
        )
        q_end = (
            time_range.end.replace(tzinfo=timezone.utc)
            if time_range.end.tzinfo is None
            else time_range.end
        )

        current_hour = search_start
        hourly_steps = []
        while current_hour <= search_end:
            hourly_steps.append(current_hour)
            current_hour += timedelta(hours=1)

        # GOES default full disk bounds: this is roughly what we fall back to if intersects=None
        default_geometry = Polygon([(-156, -81), (6, -81), (6, 81), (-156, 81)])
        geometry_to_use = intersects if intersects is not None else default_geometry

        for collection in collections:
            if not collection.startswith("ABI-L1b-Rad"):
                continue

            for satellite in requested_satellites:
                bucket = sat_to_bucket.get(satellite)
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
                            if (
                                requested_channel_ids is not None
                                and file_channel_id not in requested_channel_ids
                            ):
                                continue

                            # Granule level row
                            unique_id = hashlib.md5(
                                f"{filename}".encode("utf-8")
                            ).hexdigest()

                            rows.append(
                                {
                                    "id": unique_id,
                                    "collection": collection,
                                    "geometry": geometry_to_use,
                                    "start_time": meta["start_time"],
                                    "end_time": meta["end_time"],
                                    "href": f"s3://{f_path}",
                                    "https_url": f"https://{bucket}.s3.amazonaws.com/{f_path.replace(bucket + '/', '')}",
                                    "size_mb": f_info["size"] / (1024 * 1024),
                                    "channel_id": file_channel_id,
                                    "granule_id": filename,
                                    "satellite": satellite,
                                }
                            )

                    except FileNotFoundError as e:
                        logger.debug("S3 prefix not found", prefix=prefix, error=str(e))

        if not rows:
            return self._empty_result()

        gdf = gpd.GeoDataFrame(rows, geometry="geometry")
        return SearchResultSchema.validate(gdf)

    def _empty_result(self) -> GeoDataFrame["SearchResultSchema"]:
        columns = list(SearchResultSchema.to_schema().columns.keys())
        if "geometry" not in columns:
            columns.append("geometry")
        gdf = gpd.GeoDataFrame(columns=columns, geometry="geometry")
        return SearchResultSchema.validate(gdf)
