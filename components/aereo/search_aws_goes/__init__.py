"""Search implementation for NOAA GOES-R ABI products on AWS S3.

Provides the Pydantic-based :class:`SearchAwsGoes` plugin.
"""

from aereo.search_aws_goes.core import SearchAwsGoes
from aereo.search_aws_goes.utils import GOES_EAST_C_POLY, GOES_EAST_F_POLY, GOES_WEST_C_POLY, GOES_WEST_F_POLY

__all__ = [
    "GOES_EAST_C_POLY",
    "GOES_EAST_F_POLY",
    "GOES_WEST_C_POLY",
    "GOES_WEST_F_POLY",
    "SearchAwsGoes",
]
