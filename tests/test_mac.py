import pytest

from src.inventory.mac import InvalidMacError, mac_hyphen, normalize_mac, normalize_uuid


def test_normalize_mac_accepts_hyphen_and_colon():
    assert normalize_mac("DE-AD-BE-EF-00-01") == "de:ad:be:ef:00:01"
    assert normalize_mac("de:ad:be:ef:00:01") == "de:ad:be:ef:00:01"
    assert mac_hyphen("de:ad:be:ef:00:01") == "de-ad-be-ef-00-01"


def test_normalize_mac_rejects_short_values():
    with pytest.raises(InvalidMacError):
        normalize_mac("de:ad:be")


def test_normalize_uuid_strips_braces():
    assert normalize_uuid("{AaBbCcDd-1234}") == "aabbccdd-1234"
    assert normalize_uuid("notavailable") is None
