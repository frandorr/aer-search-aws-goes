import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import s3fs
from aer.interfaces.core import SearchProvider
from aer.schemas import AssetSchema
from pandera.typing.geopandas import GeoDataFrame
from pyresample import (
    AreaDefinition,
    load_area,  # Assuming you are using pyresample  # Assuming you are using pyresample
)
from shapely.geometry import MultiPolygon, Polygon, box
from structlog import get_logger

logger = get_logger()


# 1. Get the directory where core.py is physically located
CURRENT_DIR = Path(__file__).parent

# 2. Join that directory with your filename
AREAS_FILE = CURRENT_DIR / "areas.yaml"


def _normalize_polygon(coords: np.ndarray) -> Polygon | MultiPolygon:
    """Takes an Nx2 array of (lon, lat) boundary points, checks for antimeridian crossing,
    and returns a correctly split MultiPolygon if needed.
    """
    lons = coords[:, 0]
    lats = coords[:, 1]

    # Check if crossing antimeridian: large span of longitudes but most concentrated near +/- 180
    if np.max(lons) - np.min(lons) > 300:
        # Wrap negative longitudes to 0-360 range
        unwrapped_lons = np.where(lons < 0, lons + 360, lons)
        poly = Polygon(np.column_stack((unwrapped_lons, lats)))

        # Split it at 180
        right_clip = box(180, -90, 360, 90)
        left_clip = box(-180, -90, 180, 90)

        poly_right = poly.intersection(right_clip)
        poly_left = poly.intersection(left_clip)

        from shapely.affinity import translate

        poly_right_shifted = translate(poly_right, xoff=-360)

        # Filter out empty geometries
        geoms = []
        if not poly_left.is_empty:
            geoms.append(poly_left)
        if not poly_right_shifted.is_empty:
            if poly_right_shifted.geom_type == "MultiPolygon":
                geoms.extend(poly_right_shifted.geoms)
            else:
                geoms.append(poly_right_shifted)

        if len(geoms) > 1:
            return MultiPolygon(geoms)
        elif len(geoms) == 1:
            return geoms[0]

    return Polygon(coords)


def _get_poly_from_area(area_def: AreaDefinition, frequency: int | None = None) -> Polygon | MultiPolygon:
    from pyproj import Transformer

    transformer = Transformer.from_crs(area_def.crs, "EPSG:4326", always_xy=True)

    n = frequency * 20 if frequency else 200

    x_min, y_min, x_max, y_max = area_def.area_extent
    xs_top = np.linspace(x_min, x_max, n)
    ys_top = np.full(n, y_max)
    xs_right = np.full(n, x_max)
    ys_right = np.linspace(y_max, y_min, n)
    xs_bottom = np.linspace(x_max, x_min, n)
    ys_bottom = np.full(n, y_min)
    xs_left = np.full(n, x_min)
    ys_left = np.linspace(y_min, y_max, n)

    x_proj = np.concatenate([xs_top, xs_right, xs_bottom, xs_left])
    y_proj = np.concatenate([ys_top, ys_right, ys_bottom, ys_left])

    lons, lats = transformer.transform(x_proj, y_proj)

    mask = np.isfinite(lons) & np.isfinite(lats)
    mask &= (lons <= 180) & (lons >= -180) & (lats <= 90) & (lats >= -90)

    valid_lons = lons[mask]
    valid_lats = lats[mask]

    # For CONUS/Mesoscale, the edges are within Earth bounds, so valid_lons > 10.
    if len(valid_lons) > 10:
        coords = np.column_stack((valid_lons, valid_lats))
        return _normalize_polygon(coords)

    # For Full Disk, boundaries lie out of scope, fallback to Pyresample geostationary edge tracing.
    contour = area_def.boundary(frequency=frequency if frequency else 50).contour()
    coords = np.column_stack((contour[0], contour[1]))
    return _normalize_polygon(coords)


GOES_EAST_F_POLY = _get_poly_from_area(load_area(str(AREAS_FILE), "goes_east_abi_f_2km"))
GOES_EAST_C_POLY = _get_poly_from_area(load_area(str(AREAS_FILE), "goes_east_abi_c_2km"))

GOES_WEST_F_POLY = _get_poly_from_area(load_area(str(AREAS_FILE), "goes_west_abi_f_2km"))
GOES_WEST_C_POLY = _get_poly_from_area(load_area(str(AREAS_FILE), "goes_west_abi_p_2km"))


def _get_geometry(satellite: str, domain: str) -> Polygon | MultiPolygon | None:
    lower_satellite = satellite.lower()
    if lower_satellite == "goes-16" or lower_satellite == "goes-19":  # GOES-East
        if domain == "F":
            return GOES_EAST_F_POLY
        elif domain == "C":
            return GOES_EAST_C_POLY
    elif lower_satellite == "goes-17" or lower_satellite == "goes-18":  # GOES-West
        if domain == "F":
            return GOES_WEST_F_POLY
        elif domain == "C":
            return GOES_WEST_C_POLY
    # Default fallback geometry is None, for example Mesoscale products that don't have a defined FOV polygon.
    #  In a more advanced implementation, you might want to return a different geometry or raise an error for unknown domains.
    return None


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
        start_time = datetime.strptime(start_str, "%Y%j%H%M%S").replace(tzinfo=timezone.utc)
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


def _parse_domain(collection: str) -> str:
    """Parse the domain from a GOES product collection name."""
    domain = collection[-1]
    if domain in ["C", "F", "M"]:
        return domain
    if domain.lower() == "GLM-L2-LCFA":
        return "F"
    raise ValueError(f"Unknown GOES domain in collection name: {collection}")


def _normalize_product_name(collection: str) -> str:
    """Normalize collection names to a standard format if needed."""
    # find collection in SUPPORTED_PRODUCTS ignoring case
    for prod in SUPPORTED_PRODUCTS:
        if prod.lower() == collection.lower():
            return prod
    raise ValueError(f"Collection name '{collection}' does not match any supported GOES product.")


class AwsGoesSearchPlugin(SearchProvider, plugin_abstract=False):
    supported_collections = SUPPORTED_PRODUCTS

    def search(
        self,
        collections: list[str],
        intersects: Polygon | MultiPolygon | None = None,
        start_datetime: datetime | None = None,
        end_datetime: datetime | None = None,
        search_params: dict[str, Any] | None = None,
    ) -> GeoDataFrame[AssetSchema]:
        """Search for GOES ABI products on AWS S3.

        This plugin traverses the NOAA GOES S3 buckets (noaa-goes16, noaa-goes17, etc.)
        by year/day/hour based on the requested time range.

        When channel filters are provided via search_params["channels"],
        only files matching those bands are returned.
        """
        # normalize collectins to GOES products
        # This allows users to specify collections in a case-insensitive way, and also ensures that we only work with supported products.
        normalized_collections = []
        for col in collections:
            try:
                normalized_collections.append(_normalize_product_name(col))
            except ValueError as e:
                logger.warning("Skipping unsupported collection", collection=col, error=str(e))

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
                            if requested_channel_ids is not None and file_channel_id not in requested_channel_ids:
                                continue

                            domain = _parse_domain(collection)
                            geometry = _get_geometry(satellite, domain)
                            # Granule level row
                            unique_id = hashlib.md5(f"{filename}".encode("utf-8")).hexdigest()

                            rows.append(
                                {
                                    "id": unique_id,
                                    "collection": collection,
                                    "geometry": geometry,
                                    "start_time": meta["start_time"],
                                    "end_time": meta["end_time"],
                                    "href": f"s3://{f_path}",
                                    "https_url": f"https://{bucket}.s3.amazonaws.com/{f_path.replace(bucket + '/', '')}",
                                    "size_mb": f_info["size"] / (1024 * 1024),
                                    "channel_id": file_channel_id,
                                    "granule_id": filename,
                                    "satellite": satellite,
                                    "domain": domain,
                                }
                            )

                    except FileNotFoundError as e:
                        logger.debug("S3 prefix not found", prefix=prefix, error=str(e))

        if not rows:
            return self._empty_result()

        gdf = gpd.GeoDataFrame(rows, geometry="geometry")
        return AssetSchema.validate(gdf)

    def _empty_result(self) -> GeoDataFrame[AssetSchema]:
        columns = list(AssetSchema.to_schema().columns.keys())
        if "geometry" not in columns:
            columns.append("geometry")
        gdf = gpd.GeoDataFrame(columns=columns, geometry="geometry")
        return AssetSchema.validate(gdf)
