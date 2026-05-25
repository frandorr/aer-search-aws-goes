"""
Basic search example for aereo-search-aws-goes.

Searches for GOES-16 ABI Level-1b Radiance (CONUS) data over the
central United States for a one-hour window.
"""

from datetime import datetime, timezone
from shapely.geometry import box
from aereo.client import AerClient
from aereo.interfaces import AerProfile


def main():
    # Define an AOI over the continental US
    aoi = box(-105, 25, -85, 45)

    # Create a profile that uses the AWS GOES search plugin
    profile = AerProfile(
        name="goes_rad_c01",
        resolution=1000,
        collections={"ABI-L1b-RadC": ["C01"]},
        search_params={"satellite": "GOES-16"},
        plugin_hints={"search": "search_aws_goes"},
    )

    # Search for GOES data
    client = AerClient()
    results = client.search(
        profiles=[profile],
        intersects=aoi,
        start_datetime=datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2025, 6, 1, 13, 0, tzinfo=timezone.utc),
    )

    print(f"Found {len(results)} granules")
    if len(results) > 0:
        print(results[["collection", "start_time", "s3_url"]].head())


if __name__ == "__main__":
    main()
