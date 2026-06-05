"""Tests for simplified nodes module backward compatibility."""

from aereo.search_aws_goes.nodes import SAT_TO_BUCKET, SearchAwsGoes, supported_collections


def test_supported_collections_is_tuple_of_products() -> None:
    assert isinstance(supported_collections, tuple)
    assert "ABI-L1b-RadF" in supported_collections
    assert "ABI-L2-CMIPF" in supported_collections


def test_sat_to_bucket() -> None:
    assert "GOES-16" in SAT_TO_BUCKET
    assert SAT_TO_BUCKET["GOES-16"] == "noaa-goes16"


def test_search_aws_goes_exported() -> None:
    assert SearchAwsGoes is not None
