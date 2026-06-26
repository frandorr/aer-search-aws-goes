"""Search implementation for NOAA GOES-R ABI products on AWS S3.

Provides the ``search_aws_goes`` function plugin.
"""

from aereo.search_aws_goes.core import search_aws_goes
from aereo.search_aws_goes.utils import GOES_EAST_C_POLY, GOES_EAST_F_POLY, GOES_WEST_C_POLY, GOES_WEST_F_POLY

__all__ = [
    "GOES_EAST_C_POLY",
    "GOES_EAST_F_POLY",
    "GOES_WEST_C_POLY",
    "GOES_WEST_F_POLY",
    "search_aws_goes",
]
