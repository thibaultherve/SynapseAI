"""Tests for app.config field validators."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _build(**overrides) -> Settings:
    # _env_file=None disables .env lookup so the test doesn't inherit local
    # dev values for the field under test.
    return Settings(_env_file=None, **overrides)


def test_cors_wildcard_rejected():
    with pytest.raises(ValidationError) as exc_info:
        _build(CORS_ORIGINS=["*"])
    assert "CORS_ORIGINS cannot contain" in str(exc_info.value)


def test_cors_valid_origin_accepted():
    s = _build(CORS_ORIGINS=["https://app.example.com"])
    assert s.CORS_ORIGINS == ["https://app.example.com"]


def test_trusted_proxies_defaults_empty():
    s = _build()
    assert s.TRUSTED_PROXIES == []


def test_trusted_proxies_valid_cidr_accepted():
    s = _build(TRUSTED_PROXIES=["10.0.0.0/8", "192.168.1.1"])
    assert s.TRUSTED_PROXIES == ["10.0.0.0/8", "192.168.1.1"]


def test_trusted_proxies_invalid_rejected():
    with pytest.raises(ValidationError) as exc_info:
        _build(TRUSTED_PROXIES=["not-an-ip"])
    assert "TRUSTED_PROXIES" in str(exc_info.value)


def test_trusted_proxies_ipv6_cidr_accepted():
    s = _build(TRUSTED_PROXIES=["fd00::/8"])
    assert s.TRUSTED_PROXIES == ["fd00::/8"]


def test_log_level_default_is_info():
    s = _build()
    assert s.LOG_LEVEL == "INFO"
