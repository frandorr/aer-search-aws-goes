from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import geopandas as gpd
import s3fs
from aereo.interfaces import SearchProvider
from aereo.schemas import AssetSchema
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

SAT_TO_BUCKET = {
    "GOES-16": "noaa-goes16",
    "GOES-17": "noaa-goes17",
    "GOES-18": "noaa-goes18",
    "GOES-19": "noaa-goes19",
}


class SearchAwsGoes(SearchProvider):
    """Search provider for NOAA GOES-R ABI products on AWS S3.

    This plugin traverses the public NOAA GOES S3 buckets
    (``noaa-goes16``, ``noaa-goes17``, ``noaa-goes18``, ``noaa-goes19``)
    by year/day/hour prefix and returns matching NetCDF assets as a
    validated GeoDataFrame.
    """

    collections: list[str] | None = None
    intersects: BaseGeometry | None = None
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None
    channels: list[str] | None = None
    satellites: list[str] | None = None
    anon: bool = True
    key: str | None = None
    secret: str | None = None

    def __call__(self) -> GeoDataFrame[AssetSchema]:
        """Search for GOES ABI products on AWS S3.

        Uses instance fields to configure the search.

        Returns:
            A GeoDataFrame where each row represents a matched GOES granule
            with columns defined by :class:`aereo.schemas.AssetSchema`.
        """
        if not self.collections:
            return self.empty_result()

        supported_set = set(SUPPORTED_PRODUCTS)
        normalized_collections = []
        for col in self.collections:
            if col in supported_set:
                normalized_collections.append(col)
            else:
                logger.warning("Skipping unsupported collection", collection=col)

        if not normalized_collections:
            return self.empty_result()

        if not self.start_datetime or not self.end_datetime:
            return self.empty_result()

        fs_kwargs: dict[str, Any] = {"anon": self.anon}
        if self.key is not None:
            fs_kwargs["key"] = self.key
        if self.secret is not None:
            fs_kwargs["secret"] = self.secret
        fs = s3fs.S3FileSystem(**fs_kwargs)

        requested_satellites = set(self.satellites) if self.satellites else set(SAT_TO_BUCKET.keys())

        requested_channel_ids: set[str] | None = None
        if self.channels:
            profile_channels: set[str] = set()
            for ch in self.channels:
                ch_str = str(ch).upper()
                if ch_str.startswith("C"):
                    ch_str = ch_str[1:]
                try:
                    profile_channels.add(str(int(ch_str)))
                except ValueError:
                    profile_channels.add(str(ch))
            requested_channel_ids = profile_channels if profile_channels else None

        q_start = self.start_datetime
        if q_start.tzinfo is None:
            q_start = q_start.replace(tzinfo=timezone.utc)
        q_end = self.end_datetime
        if q_end.tzinfo is None:
            q_end = q_end.replace(tzinfo=timezone.utc)

        search_start = q_start.replace(minute=0, second=0, microsecond=0)
        search_end = q_end

        hourly_steps = []
        current_hour = search_start
        while current_hour <= search_end:
            hourly_steps.append(current_hour)
            current_hour += timedelta(hours=1)

        rows: list[dict[str, Any]] = []

        for collection in normalized_collections:
            for satellite in requested_satellites:
                bucket = SAT_TO_BUCKET.get(satellite)
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
            return self.empty_result()

        gdf = gpd.GeoDataFrame(rows, geometry="geometry")
        return cast(GeoDataFrame, AssetSchema.validate(gdf))
