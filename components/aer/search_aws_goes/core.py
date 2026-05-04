import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast, override

import geopandas as gpd
import s3fs
from aer.interfaces import SearchProvider
from aer.schemas import AssetSchema
from pandera.typing.geopandas import GeoDataFrame
from shapely.geometry.base import BaseGeometry
from structlog import get_logger

from .utils import _get_geometry, _parse_domain, _parse_goes_filename

logger = get_logger()

CURRENT_DIR = Path(__file__).parent
AREAS_FILE = CURRENT_DIR / "areas.yaml"


SUPPORTED_PRODUCTS = [
    "ABI-L1b-RadC",
    "ABI-L1b-RadF",
    "ABI-L1b-RadM",
    "ABI-L2-ACHA2KMC",
    "ABI-L2-ACHA2KMF",
    "ABI-L2-ACHA2KMM",
    "ABI-L2-ACHAC",
    "ABI-L2-ACHAF",
    "ABI-L2-ACHAM",
    "ABI-L2-ACHP2KMC",
    "ABI-L2-ACHP2KMF",
    "ABI-L2-ACHP2KMM",
    "ABI-L2-ACHTF",
    "ABI-L2-ACHTM",
    "ABI-L2-ACMC",
    "ABI-L2-ACMF",
    "ABI-L2-ACMM",
    "ABI-L2-ACTPC",
    "ABI-L2-ACTPF",
    "ABI-L2-ACTPM",
    "ABI-L2-ADPC",
    "ABI-L2-ADPF",
    "ABI-L2-ADPM",
    "ABI-L2-AICEF",
    "ABI-L2-AITAF",
    "ABI-L2-AODC",
    "ABI-L2-AODF",
    "ABI-L2-BRFC",
    "ABI-L2-BRFF",
    "ABI-L2-BRFM",
    "ABI-L2-CCLC",
    "ABI-L2-CCLF",
    "ABI-L2-CCLM",
    "ABI-L2-CMIPC",
    "ABI-L2-CMIPF",
    "ABI-L2-CMIPM",
    "ABI-L2-COD2KMF",
    "ABI-L2-CODC",
    "ABI-L2-CODF",
    "ABI-L2-CPSC",
    "ABI-L2-CPSF",
    "ABI-L2-CPSM",
    "ABI-L2-CTPC",
    "ABI-L2-CTPF",
    "ABI-L2-DMWC",
    "ABI-L2-DMWF",
    "ABI-L2-DMWM",
    "ABI-L2-DMWVC",
    "ABI-L2-DMWVF",
    "ABI-L2-DMWVM",
    "ABI-L2-DSRC",
    "ABI-L2-DSRF",
    "ABI-L2-DSRM",
    "ABI-L2-DSIC",
    "ABI-L2-DSIF",
    "ABI-L2-DSIM",
    "ABI-L2-FDC",
    "ABI-L2-FDF",
    "ABI-L2-FDM",
    "ABI-L2-FSCC",
    "ABI-L2-FSCF",
    "ABI-L2-FSCM",
    "ABI-L2-LSAC",
    "ABI-L2-LSAF",
    "ABI-L2-LSAM",
    "ABI-L2-LSTC",
    "ABI-L2-LSTF",
    "ABI-L2-LSTM",
    "ABI-L2-LVMPC",
    "ABI-L2-LVMPF",
    "ABI-L2-LVMPM",
    "ABI-L2-LVTPC",
    "ABI-L2-LVTPF",
    "ABI-L2-LVTPM",
    "ABI-L2-MCMIPC",
    "ABI-L2-MCMIPF",
    "ABI-L2-MCMIPM",
    "ABI-L2-RRQPEF",
    "ABI-L2-RSRC",
    "ABI-L2-RSRF",
    "ABI-L2-SSTF",
    "ABI-L2-TPWC",
    "ABI-L2-TPWF",
    "ABI-L2-TPWM",
    "ABI-L2-VAAF",
    "GLM-L2-LCFA",
]


class AwsGoesSearchPlugin(SearchProvider, plugin_abstract=False):
    supported_collections: Sequence[str] = SUPPORTED_PRODUCTS

    @override
    def search(
        self,
        collections: Sequence[str],
        intersects: BaseGeometry | None = None,
        start_datetime: datetime | None = None,
        end_datetime: datetime | None = None,
        search_params: Mapping[str, Any] | None = None,
    ) -> GeoDataFrame[AssetSchema]:
        """Search for GOES ABI products on AWS S3.

        This plugin traverses the NOAA GOES S3 buckets (noaa-goes16, noaa-goes17, etc.)
        by year/day/hour based on the requested time range.

        When channel filters are provided via search_params["channels"],
        only files matching those bands are returned.
        """
        # Collections are already mapped to supported_collections case by AerClient
        # Validate against SUPPORTED_PRODUCTS directly
        normalized_collections = []
        supported_set = set(SUPPORTED_PRODUCTS)
        for col in collections:
            if col in supported_set:
                normalized_collections.append(col)
            else:
                logger.warning("Skipping unsupported collection", collection=col)

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
        elif "satellite" in search_params:
            sat = search_params["satellite"].upper()
            requested_satellites = {sat}

        if not start_datetime or not end_datetime:
            return self._empty_result()

        search_start = start_datetime.replace(minute=0, second=0, microsecond=0)
        search_end = end_datetime

        q_start = start_datetime
        if q_start.tzinfo is None:
            q_start = q_start.replace(tzinfo=timezone.utc)
        q_end = end_datetime
        if q_end.tzinfo is None:
            q_end = q_end.replace(tzinfo=timezone.utc)

        current_hour = search_start
        hourly_steps = []
        while current_hour <= search_end:
            hourly_steps.append(current_hour)
            current_hour += timedelta(hours=1)

        for collection in normalized_collections:
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

                            domain = _parse_domain(collection)
                            geometry = _get_geometry(satellite, domain)
                            # Granule level row
                            # from pathlib import Path

                            granule_id = Path(filename).stem

                            rows.append(
                                {
                                    "id": granule_id,
                                    "collection": collection,
                                    "geometry": geometry,
                                    "start_time": meta["start_time"],
                                    "end_time": meta["end_time"],
                                    "href": f"s3://{f_path}",
                                    "https_url": f"https://{bucket}.s3.amazonaws.com/{f_path.replace(bucket + '/', '')}",
                                    "size_mb": f_info["size"] / (1024 * 1024),
                                    "channel_id": file_channel_id,
                                    "granule_id": granule_id,
                                    "satellite": satellite,
                                    "domain": domain,
                                }
                            )

                    except FileNotFoundError as e:
                        logger.debug("S3 prefix not found", prefix=prefix, error=str(e))

        if not rows:
            return self._empty_result()

        gdf = gpd.GeoDataFrame(rows, geometry="geometry")
        return cast(GeoDataFrame, AssetSchema.validate(gdf))

    def _empty_result(self) -> GeoDataFrame[AssetSchema]:
        columns = list(AssetSchema.to_schema().columns.keys())
        if "geometry" not in columns:
            columns.append("geometry")
        gdf = gpd.GeoDataFrame(columns=columns, geometry="geometry")
        return cast(GeoDataFrame, AssetSchema.validate(gdf))
