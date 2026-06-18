from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from pyresample import load_area
from pyresample.geometry import AreaDefinition
from shapely.geometry import MultiPolygon, Polygon, box

CURRENT_DIR = Path(__file__).parent
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
    contour = area_def.boundary(vertices_per_side=frequency if frequency else 50).contour()
    coords = np.column_stack((contour[0], contour[1]))
    return _normalize_polygon(coords)


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
    if collection.lower() == "glm-l2-lcfa":
        return "F"
    domain = collection[-1]
    if domain in ["C", "F", "M"]:
        return domain
    raise ValueError(f"Unknown GOES domain in collection name: {collection}")


GOES_EAST_F_POLY = _get_poly_from_area(load_area(str(AREAS_FILE), "goes_east_abi_f_2km"))
GOES_EAST_C_POLY = _get_poly_from_area(load_area(str(AREAS_FILE), "goes_east_abi_c_2km"))

GOES_WEST_F_POLY = _get_poly_from_area(load_area(str(AREAS_FILE), "goes_west_abi_f_2km"))
GOES_WEST_C_POLY = _get_poly_from_area(load_area(str(AREAS_FILE), "goes_west_abi_p_2km"))
