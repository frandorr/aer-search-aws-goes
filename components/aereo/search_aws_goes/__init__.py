"""Search implementation for NOAA GOES-R ABI products on AWS S3.

Provides both the legacy class-based API (:class:`AwsGoesSearchPlugin`)
and the new function-based Hamilton nodes.
"""

from aereo.search_aws_goes.core import AwsGoesSearchPlugin
from aereo.search_aws_goes.nodes import (
    SAT_TO_BUCKET,
    search_assets,
    search_results,
    supported_collections,
)
from aereo.search_aws_goes.utils import GOES_EAST_C_POLY, GOES_WEST_F_POLY

__all__ = [
    "AwsGoesSearchPlugin",
    "GOES_EAST_C_POLY",
    "GOES_WEST_F_POLY",
    "SAT_TO_BUCKET",
    "search_assets",
    "search_results",
    "supported_collections",
]
