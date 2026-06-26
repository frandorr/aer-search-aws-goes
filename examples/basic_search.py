"""
Basic search example for aereo-search-aws-goes.

Searches for GOES-16 ABI Level-1b Radiance (CONUS) data over the
central United States for a one-hour window.
"""

from datetime import datetime, timezone
from shapely.geometry import box
from aereo.search_aws_goes import search_aws_goes


def main():
    # Define an AOI over the continental US
    aoi = box(-105, 25, -85, 45)

    results = search_aws_goes(
        collections={"ABI-L1b-RadC": ["C01"]},
        intersects=aoi,
        start_datetime=datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2025, 6, 1, 13, 0, tzinfo=timezone.utc),
        satellites=["GOES-16"],
    )

    print(f"Found {len(results)} granules")
    if len(results) > 0:
        print(results[["collection", "start_time", "href"]].head())


if __name__ == "__main__":
    main()
