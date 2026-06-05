"""Simplified nodes module for backward compatibility.

Re-exports :class:`SearchAwsGoes` from the core module.
"""

from aereo.search_aws_goes.core import SAT_TO_BUCKET, SearchAwsGoes, SUPPORTED_PRODUCTS

supported_collections = tuple(SUPPORTED_PRODUCTS)

__all__ = [
    "SAT_TO_BUCKET",
    "SearchAwsGoes",
    "supported_collections",
]
