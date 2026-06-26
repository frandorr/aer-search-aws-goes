"""Simplified nodes module for backward compatibility.

Re-exports ``search_aws_goes`` and constants from the core module.
"""

from aereo.search_aws_goes.core import SAT_TO_BUCKET, SUPPORTED_PRODUCTS, search_aws_goes

supported_collections = tuple(SUPPORTED_PRODUCTS)

__all__ = [
    "SAT_TO_BUCKET",
    "search_aws_goes",
    "supported_collections",
]
